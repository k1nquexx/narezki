import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Cpu,
  ExternalLink,
  FolderOpen,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Share2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import { isTauriRuntime, pickDirectory, pickWeightsFile } from "../lib/tauri";
import { useAppStore } from "../store/useAppStore";
import type {
  AppSettings,
  LLMTestResult,
  SocialAccount,
  SocialAuthType,
  SocialPlatform,
} from "../types/api";

const LLM_PROVIDERS = [
  { label: "OpenRouter", url: "https://openrouter.ai/api/v1", model: "deepseek/deepseek-chat" },
  { label: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
];

export function SettingsPage() {
  const settings = useAppStore((s) => s.settings);
  const encoderProbe = useAppStore((s) => s.encoderProbe);
  const gpuInfo = useAppStore((s) => s.gpuInfo);
  const socialAccounts = useAppStore((s) => s.socialAccounts);
  const publishQueue = useAppStore((s) => s.publishQueue);
  const saveSettings = useAppStore((s) => s.saveSettings);
  const refreshPublishing = useAppStore((s) => s.refreshPublishing);
  const addToast = useAppStore((s) => s.addToast);

  const [subTab, setSubTab] = useState<"system" | "publishing">("system");
  const [form, setForm] = useState<AppSettings | null>(settings);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<LLMTestResult | null>(null);

  const [accPlatform, setAccPlatform] = useState<SocialPlatform>("youtube");
  const [accName, setAccName] = useState("");
  const [accAuthType, setAccAuthType] = useState<SocialAuthType>("token");
  const [accAccessToken, setAccAccessToken] = useState("");
  const [accRefreshToken, setAccRefreshToken] = useState("");
  const [accIgUserId, setAccIgUserId] = useState("");
  const [accCookies, setAccCookies] = useState("");
  const [accPrivacy, setAccPrivacy] = useState<"public" | "unlisted" | "private">("public");
  const [accGapMinutes, setAccGapMinutes] = useState(120);
  const [accDailyCap, setAccDailyCap] = useState(6);
  const [addingAccount, setAddingAccount] = useState(false);

  useEffect(() => {
    if (settings) setForm(settings);
  }, [settings]);

  useEffect(() => {
    if (subTab === "publishing") {
      void refreshPublishing();
    }
  }, [subTab, refreshPublishing]);

  if (!form) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-zinc-400">
        Загрузка настроек...
      </div>
    );
  }

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveSettings(form);
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось сохранить настройки",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTestLlm = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testLlm({
        base_url: form.llm_base_url,
        api_key: form.llm_api_key,
        model: form.llm_model,
      });
      setTestResult(res);
    } catch (err) {
      setTestResult({
        ok: false,
        model: form.llm_model,
        latency_ms: 0,
        reply: "",
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTesting(false);
    }
  };

  const handlePickExportDir = async () => {
    const dir = await pickDirectory("Выберите папку для экспорта клипов");
    if (dir) setForm({ ...form, export_dir: dir });
  };

  const handlePickWeights = async () => {
    const file = await pickWeightsFile();
    if (file) setForm({ ...form, yolo_model_path: file });
  };

  const handleStartOAuth = async (platform: SocialPlatform) => {
    try {
      await saveSettings(form);
      const { url } = await api.getOAuthUrl(platform);
      window.open(url, "_blank", "noopener,noreferrer");
      addToast({
        type: "info",
        title: `Открыта авторизация ${platform}`,
        message: "Подтвердите доступ в браузере и нажмите «Обновить»",
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка запуска OAuth2",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleCreateAccount = async () => {
    if (!accName.trim()) {
      addToast({ type: "warning", title: "Укажите название аккаунта" });
      return;
    }
    const creds: Record<string, unknown> = {};
    if (accAccessToken.trim()) creds.access_token = accAccessToken.trim();
    if (accRefreshToken.trim()) creds.refresh_token = accRefreshToken.trim();
    if (accIgUserId.trim()) creds.ig_user_id = accIgUserId.trim();
    if (accCookies.trim()) creds.cookies = accCookies.trim();
    if (Object.keys(creds).length === 0) {
      addToast({ type: "warning", title: "Укажите access_token, refresh_token или cookies" });
      return;
    }
    setAddingAccount(true);
    try {
      await api.createSocialAccount({
        platform: accPlatform,
        name: accName.trim(),
        auth_type: accAuthType,
        credentials_json: creds,
        default_privacy: accPrivacy,
        post_gap_minutes: accGapMinutes,
        daily_cap: accDailyCap,
      });
      setAccName("");
      setAccAccessToken("");
      setAccRefreshToken("");
      setAccIgUserId("");
      setAccCookies("");
      await refreshPublishing();
      addToast({ type: "success", title: "Аккаунт публикации добавлен" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось добавить аккаунт",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setAddingAccount(false);
    }
  };

  const handleToggleAccount = async (acc: SocialAccount) => {
    try {
      await api.updateSocialAccount(acc.id, { enabled: !acc.enabled });
      await refreshPublishing();
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка изменения аккаунта",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleDeleteAccount = async (id: string) => {
    try {
      await api.deleteSocialAccount(id);
      await refreshPublishing();
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка удаления аккаунта",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">Настройки системы, AI и публикаций</h1>
            <p className="text-xs text-zinc-400 mt-1">
              Cloud LLM провайдер, Whisper на CUDA, NVENC, YOLOv8 и расписание автопостинга
            </p>
          </div>

          <div className="flex items-center gap-2">
            <div className="inline-flex rounded-lg bg-surface-900 border border-surface-800 p-0.5 text-xs">
              <button
                onClick={() => setSubTab("system")}
                className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
                  subTab === "system"
                    ? "bg-accent-600 text-white"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                Система и AI
              </button>
              <button
                onClick={() => setSubTab("publishing")}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium transition-colors ${
                  subTab === "publishing"
                    ? "bg-accent-600 text-white"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                <Share2 className="w-3.5 h-3.5" />
                Публикация и Автопостинг
              </button>
            </div>

            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-xs font-semibold text-white shadow-lg shadow-accent-600/20"
            >
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              Сохранить настройки
            </button>
          </div>
        </div>

        {subTab === "system" ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Cloud LLM */}
          <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                <KeyRound className="w-4 h-4 text-accent-400" />
                <span>Cloud LLM API (Поиск хайлайтов)</span>
              </div>
              <div className="flex gap-1.5">
                {LLM_PROVIDERS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() =>
                      setForm({ ...form, llm_base_url: p.url, llm_model: p.model })
                    }
                    className="px-2 py-0.5 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-[11px] text-zinc-300"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-3 text-xs">
              <label className="block space-y-1">
                <span className="text-zinc-400">Base URL (OpenAI-compatible)</span>
                <input
                  value={form.llm_base_url}
                  onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100 focus:outline-none focus:border-accent-500"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-zinc-400">API Key</span>
                <input
                  type="password"
                  value={form.llm_api_key}
                  onChange={(e) => setForm({ ...form, llm_api_key: e.target.value })}
                  placeholder="sk-..."
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100 focus:outline-none focus:border-accent-500"
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block space-y-1">
                  <span className="text-zinc-400">Модель</span>
                  <input
                    value={form.llm_model}
                    onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                    className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100 focus:outline-none focus:border-accent-500"
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-zinc-400">Макс. клипов</span>
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={form.llm_max_highlights}
                    onChange={(e) =>
                      setForm({ ...form, llm_max_highlights: Number(e.target.value) || 10 })
                    }
                    className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100 focus:outline-none focus:border-accent-500"
                  />
                </label>
              </div>

              <div className="flex items-center gap-3 pt-1">
                <button
                  onClick={handleTestLlm}
                  disabled={testing}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-xs font-medium text-zinc-200"
                >
                  {testing ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="w-3.5 h-3.5 text-accent-400" />
                  )}
                  Проверить соединение
                </button>
                {testResult && (
                  <span
                    className={`text-xs ${
                      testResult.ok ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {testResult.ok
                      ? `OK (${testResult.latency_ms} мс): ${testResult.reply}`
                      : testResult.error}
                  </span>
                )}
              </div>
            </div>
          </section>

          {/* GPU & Whisper */}
          <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <Cpu className="w-4 h-4 text-accent-400" />
              <span>GPU, Whisper и Энкодер</span>
            </div>

            {gpuInfo && (
              <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 text-xs flex items-center justify-between">
                <span className="text-zinc-300">{gpuInfo.device_name}</span>
                <span className="text-emerald-400 tabular-nums">
                  {gpuInfo.vram_used_mb} / {gpuInfo.vram_total_mb} MB VRAM
                </span>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3 text-xs">
              <label className="block space-y-1">
                <span className="text-zinc-400">Модель Whisper</span>
                <select
                  value={form.whisper_model_size}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      whisper_model_size: e.target.value as AppSettings["whisper_model_size"],
                    })
                  }
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                >
                  <option value="large-v3-turbo">large-v3-turbo (рекомендуется)</option>
                  <option value="medium">medium</option>
                  <option value="small">small</option>
                  <option value="base">base (быстрая)</option>
                  <option value="large-v3">large-v3</option>
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-zinc-400">Точность / Compute Type</span>
                <select
                  value={form.whisper_compute_type}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      whisper_compute_type: e.target.value as AppSettings["whisper_compute_type"],
                    })
                  }
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                >
                  <option value="float16">float16 (GPU CUDA)</option>
                  <option value="int8_float16">int8_float16 (экономия VRAM)</option>
                  <option value="int8">int8 (CPU)</option>
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-zinc-400">Видео-кодировщик</span>
                <select
                  value={form.preferred_encoder}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      preferred_encoder: e.target.value as AppSettings["preferred_encoder"],
                    })
                  }
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                >
                  <option value="auto">
                    Авто ({encoderProbe?.selected ?? "определяется"})
                  </option>
                  <option value="h264_nvenc">h264_nvenc (NVIDIA)</option>
                  <option value="hevc_nvenc">hevc_nvenc (NVIDIA HEVC)</option>
                  <option value="h264_amf">h264_amf (AMD)</option>
                  <option value="h264_qsv">h264_qsv (Intel)</option>
                  <option value="libx264">libx264 (CPU)</option>
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-zinc-400">Качество (CQ / CRF: {form.crf_or_cq})</span>
                <input
                  type="range"
                  min={14}
                  max={30}
                  value={form.crf_or_cq}
                  onChange={(e) => setForm({ ...form, crf_or_cq: Number(e.target.value) })}
                  className="w-full accent-accent-500 mt-2"
                />
              </label>
            </div>

            {encoderProbe && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {Object.entries(encoderProbe.details).map(([enc, ok]) => (
                  <span
                    key={enc}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] border ${
                      ok
                        ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                        : "bg-surface-950 border-surface-800 text-zinc-500"
                    }`}
                  >
                    {ok && <CheckCircle2 className="w-3 h-3" />}
                    {enc}
                  </span>
                ))}
              </div>
            )}
          </section>

          {/* Tracker & Export Paths */}
          <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-3 text-xs lg:col-span-2">
            <h2 className="text-sm font-semibold text-white">Трекинг YOLOv8 и пути экспорта</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="block space-y-1">
                <span className="text-zinc-400">Веса YOLOv8 (.pt, стандартные или для аниме-лиц)</span>
                <div className="flex gap-2">
                  <input
                    value={form.yolo_model_path}
                    onChange={(e) => setForm({ ...form, yolo_model_path: e.target.value })}
                    className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                  />
                  {isTauriRuntime() && (
                    <button
                      onClick={handlePickWeights}
                      className="px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200"
                    >
                      <FolderOpen className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </label>

              <label className="block space-y-1">
                <span className="text-zinc-400">Папка экспорта клипов (.mp4 + .txt)</span>
                <div className="flex gap-2">
                  <input
                    value={form.export_dir}
                    onChange={(e) => setForm({ ...form, export_dir: e.target.value })}
                    className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                  />
                  {isTauriRuntime() && (
                    <button
                      onClick={handlePickExportDir}
                      className="px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200"
                    >
                      <FolderOpen className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </label>
            </div>
          </section>
        </div>
        ) : (
          <div className="space-y-5 text-xs">
            {/* Global Mode & Anti-Shadowban Policy */}
            <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white">
                  Режим экспорта и автопостинга
                </h2>
                <label className="inline-flex items-center gap-2 cursor-pointer text-zinc-300">
                  <input
                    type="checkbox"
                    checked={form.publish_paused}
                    onChange={(e) => setForm({ ...form, publish_paused: e.target.checked })}
                    className="accent-amber-500"
                  />
                  Пауза очереди публикаций
                </label>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setForm({ ...form, publish_mode: "local_only" })}
                  className={`p-4 rounded-xl border text-left transition-colors ${
                    form.publish_mode === "local_only"
                      ? "border-accent-500 bg-accent-600/15 text-white"
                      : "border-surface-800 bg-surface-950 text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <div className="font-semibold text-sm">Только локальный экспорт</div>
                  <p className="text-[11px] opacity-80 mt-1">
                    Клипы (включая Live Monitor) сохраняются в папку экспорта (.mp4 + .txt) без отправки в соцсети.
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => setForm({ ...form, publish_mode: "auto_publish" })}
                  className={`p-4 rounded-xl border text-left transition-colors ${
                    form.publish_mode === "auto_publish"
                      ? "border-emerald-500 bg-emerald-500/15 text-white"
                      : "border-surface-800 bg-surface-950 text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <div className="font-semibold text-sm">Автопостинг по расписанию</div>
                  <p className="text-[11px] opacity-80 mt-1">
                    Планировщик ставит клипы в очередь с паузой от теневого бана (YouTube Shorts, TikTok, Reels).
                  </p>
                </button>
              </div>

              <label className="inline-flex items-center gap-2 cursor-pointer text-zinc-100 pt-1">
                <input
                  type="checkbox"
                  checked={form.live_auto_publish}
                  onChange={(e) => setForm({ ...form, live_auto_publish: e.target.checked })}
                  className="accent-accent-500"
                />
                Автоматически публиковать лайв-клипы после рендера
              </label>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-1">
                <label className="block space-y-1">
                  <span className="text-zinc-400">Интервал между постами (мин)</span>
                  <input
                    type="number"
                    min={5}
                    max={1440}
                    value={form.publish_default_gap_minutes}
                    onChange={(e) =>
                      setForm({ ...form, publish_default_gap_minutes: Number(e.target.value) })
                    }
                    className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                  />
                </label>

                <label className="block space-y-1">
                  <span className="text-zinc-400">Лимит постов в сутки / аккаунт</span>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={form.publish_default_daily_cap}
                    onChange={(e) =>
                      setForm({ ...form, publish_default_daily_cap: Number(e.target.value) })
                    }
                    className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                  />
                </label>

                <label className="block space-y-1">
                  <span className="text-zinc-400">Public HTTPS URL (для IG Reels)</span>
                  <input
                    value={form.publish_public_base_url}
                    onChange={(e) =>
                      setForm({ ...form, publish_public_base_url: e.target.value })
                    }
                    placeholder="https://my-tunnel.trycloudflare.com"
                    className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                  />
                </label>
              </div>
            </section>

            {/* OAuth App Credentials */}
            <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-4">
              <h2 className="text-sm font-semibold text-white">
                OAuth2 приложения платформ (YouTube / TikTok / Instagram)
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-rose-300">YouTube Shorts</span>
                    <button
                      type="button"
                      onClick={() => void handleStartOAuth("youtube")}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200 text-[11px]"
                    >
                      <ExternalLink className="w-3 h-3" /> OAuth
                    </button>
                  </div>
                  <input
                    value={form.youtube_client_id}
                    onChange={(e) => setForm({ ...form, youtube_client_id: e.target.value })}
                    placeholder="Google Client ID"
                    className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                  <input
                    type="password"
                    value={form.youtube_client_secret}
                    onChange={(e) => setForm({ ...form, youtube_client_secret: e.target.value })}
                    placeholder="Google Client Secret"
                    className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                </div>

                <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-zinc-100">TikTok</span>
                    <button
                      type="button"
                      onClick={() => void handleStartOAuth("tiktok")}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200 text-[11px]"
                    >
                      <ExternalLink className="w-3 h-3" /> OAuth
                    </button>
                  </div>
                  <input
                    value={form.tiktok_client_key}
                    onChange={(e) => setForm({ ...form, tiktok_client_key: e.target.value })}
                    placeholder="TikTok Client Key"
                    className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                  <input
                    type="password"
                    value={form.tiktok_client_secret}
                    onChange={(e) => setForm({ ...form, tiktok_client_secret: e.target.value })}
                    placeholder="TikTok Client Secret"
                    className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                </div>

                <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-pink-300">Instagram Reels</span>
                    <button
                      type="button"
                      onClick={() => void handleStartOAuth("instagram")}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200 text-[11px]"
                    >
                      <ExternalLink className="w-3 h-3" /> OAuth
                    </button>
                  </div>
                  <input
                    value={form.instagram_app_id}
                    onChange={(e) => setForm({ ...form, instagram_app_id: e.target.value })}
                    placeholder="Meta App ID"
                    className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                  <input
                    type="password"
                    value={form.instagram_app_secret}
                    onChange={(e) => setForm({ ...form, instagram_app_secret: e.target.value })}
                    placeholder="Meta App Secret"
                    className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                </div>
              </div>
            </section>

            {/* Manual Account Add (Token / Cookies) & List */}
            <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white">
                  Аккаунты для публикации ({socialAccounts.length})
                </h2>
                <button
                  type="button"
                  onClick={() => void refreshPublishing()}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-300"
                >
                  <RefreshCw className="w-3 h-3" /> Обновить
                </button>
              </div>

              <div className="p-3.5 rounded-lg bg-surface-950 border border-surface-800 space-y-3">
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2.5">
                  <select
                    value={accPlatform}
                    onChange={(e) => setAccPlatform(e.target.value as SocialPlatform)}
                    className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  >
                    <option value="youtube">YouTube Shorts</option>
                    <option value="tiktok">TikTok</option>
                    <option value="instagram">Instagram Reels</option>
                  </select>

                  <input
                    value={accName}
                    onChange={(e) => setAccName(e.target.value)}
                    placeholder="Название канала/аккаунта"
                    className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100 md:col-span-2"
                  />

                  <select
                    value={accAuthType}
                    onChange={(e) => setAccAuthType(e.target.value as SocialAuthType)}
                    className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  >
                    <option value="token">API Token / Refresh</option>
                    <option value="cookies">Cookies / Session</option>
                    <option value="oauth">OAuth2 Token</option>
                  </select>

                  <select
                    value={accPrivacy}
                    onChange={(e) =>
                      setAccPrivacy(e.target.value as "public" | "unlisted" | "private")
                    }
                    className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  >
                    <option value="public">Public</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="private">Private</option>
                  </select>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
                  <input
                    type="password"
                    value={accAccessToken}
                    onChange={(e) => setAccAccessToken(e.target.value)}
                    placeholder="Access Token"
                    className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                  <input
                    type="password"
                    value={accRefreshToken}
                    onChange={(e) => setAccRefreshToken(e.target.value)}
                    placeholder="Refresh Token (YouTube / TikTok)"
                    className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                  />
                  {accPlatform === "instagram" ? (
                    <input
                      value={accIgUserId}
                      onChange={(e) => setAccIgUserId(e.target.value)}
                      placeholder="IG User ID"
                      className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                    />
                  ) : (
                    <input
                      value={accCookies}
                      onChange={(e) => setAccCookies(e.target.value)}
                      placeholder="Cookies JSON / header (опционально)"
                      className="px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                    />
                  )}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <label className="inline-flex items-center gap-1.5 text-zinc-400">
                      <span>Пауза (мин):</span>
                      <input
                        type="number"
                        min={5}
                        max={1440}
                        value={accGapMinutes}
                        onChange={(e) => setAccGapMinutes(Number(e.target.value))}
                        className="w-16 px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                      />
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-zinc-400">
                      <span>Лимит/день:</span>
                      <input
                        type="number"
                        min={1}
                        max={50}
                        value={accDailyCap}
                        onChange={(e) => setAccDailyCap(Number(e.target.value))}
                        className="w-16 px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                      />
                    </label>
                  </div>

                  <button
                    type="button"
                    onClick={() => void handleCreateAccount()}
                    disabled={addingAccount}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 text-white font-medium"
                  >
                    <Plus className="w-3.5 h-3.5" /> Добавить аккаунт
                  </button>
                </div>
              </div>

              {socialAccounts.length > 0 && (
                <div className="divide-y divide-surface-800">
                  {socialAccounts.map((acc) => (
                    <div key={acc.id} className="py-2.5 flex items-center justify-between gap-3">
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-2">
                          <span className="px-1.5 py-0.5 rounded bg-surface-800 border border-surface-700 text-[10px] uppercase text-zinc-200">
                            {acc.platform}
                          </span>
                          <span className="font-semibold text-zinc-100">{acc.name}</span>
                          <span className="text-[11px] text-zinc-400">({acc.auth_type})</span>
                          {acc.has_credentials && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                              Токен сохранён
                            </span>
                          )}
                        </div>
                        <p className="text-[11px] text-zinc-400">
                          Приватность: {acc.default_privacy} • Интервал: {acc.post_gap_minutes} мин •
                          Лимит: {acc.daily_cap}/сут
                        </p>
                      </div>

                      <div className="flex items-center gap-2">
                        <label className="inline-flex items-center gap-1.5 text-zinc-300 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={acc.enabled}
                            onChange={() => void handleToggleAccount(acc)}
                            className="accent-accent-500"
                          />
                          Активен
                        </label>
                        <button
                          type="button"
                          onClick={() => void handleDeleteAccount(acc.id)}
                          className="p-1.5 rounded bg-surface-800 hover:bg-rose-500/20 border border-surface-700 text-zinc-400 hover:text-rose-300"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Scheduled Publish Queue */}
            <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-3">
              <h2 className="text-sm font-semibold text-white">
                Очередь публикаций ({publishQueue.length})
              </h2>

              {publishQueue.length === 0 ? (
                <p className="text-zinc-400 py-2">Очередь публикаций пуста.</p>
              ) : (
                <div className="divide-y divide-surface-800">
                  {publishQueue.map((item) => (
                    <div
                      key={item.id}
                      className="py-2.5 flex flex-wrap items-center justify-between gap-3"
                    >
                      <div className="space-y-0.5 min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="px-1.5 py-0.5 rounded bg-surface-800 border border-surface-700 text-[10px] uppercase text-zinc-200">
                            {item.platform}
                          </span>
                          <span className="font-medium text-zinc-100 truncate">{item.title}</span>
                          <span className="text-[11px] text-zinc-400">→ {item.account_name}</span>
                          <span className="px-1.5 py-0.5 rounded bg-surface-950 border border-surface-800 text-[10px] text-zinc-300">
                            {item.status}
                          </span>
                        </div>
                        <p className="text-[11px] text-zinc-400">
                          Запланировано: {new Date(item.scheduled_for).toLocaleString()} • Попытки:{" "}
                          {item.tries}
                          {item.error && (
                            <span className="ml-2 text-rose-400">Ошибка: {item.error}</span>
                          )}
                        </p>
                      </div>

                      <div className="flex items-center gap-1.5">
                        {item.remote_url && (
                          <a
                            href={item.remote_url}
                            target="_blank"
                            rel="noreferrer"
                            className="p-1.5 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-emerald-300"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </a>
                        )}
                        {(item.status === "failed" || item.status === "cancelled") && (
                          <button
                            type="button"
                            onClick={() =>
                              void api.retryPublishItem(item.id).then(() => refreshPublishing())
                            }
                            title="Повторить сейчас"
                            className="p-1.5 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {item.status === "scheduled" && (
                          <button
                            type="button"
                            onClick={() =>
                              void api.cancelPublishItem(item.id).then(() => refreshPublishing())
                            }
                            title="Отменить"
                            className="p-1.5 rounded bg-surface-800 hover:bg-amber-500/20 border border-surface-700 text-amber-300"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() =>
                            void api.deletePublishItem(item.id).then(() => refreshPublishing())
                          }
                          title="Удалить из истории"
                          className="p-1.5 rounded bg-surface-800 hover:bg-rose-500/20 border border-surface-700 text-zinc-400 hover:text-rose-300"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
