import { useEffect, useState } from "react";
import { CheckCircle2, Cpu, FolderOpen, KeyRound, Loader2, Save, Sparkles } from "lucide-react";
import { api } from "../lib/api";
import { isTauriRuntime, pickDirectory, pickWeightsFile } from "../lib/tauri";
import { useAppStore } from "../store/useAppStore";
import type { AppSettings, LLMTestResult } from "../types/api";

const LLM_PROVIDERS = [
  { label: "OpenRouter", url: "https://openrouter.ai/api/v1", model: "deepseek/deepseek-chat" },
  { label: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
];

export function SettingsPage() {
  const settings = useAppStore((s) => s.settings);
  const encoderProbe = useAppStore((s) => s.encoderProbe);
  const gpuInfo = useAppStore((s) => s.gpuInfo);
  const saveSettings = useAppStore((s) => s.saveSettings);
  const addToast = useAppStore((s) => s.addToast);

  const [form, setForm] = useState<AppSettings | null>(settings);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<LLMTestResult | null>(null);

  useEffect(() => {
    if (settings) setForm(settings);
  }, [settings]);

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

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-white">Настройки системы и AI</h1>
            <p className="text-xs text-zinc-400 mt-1">
              Cloud LLM провайдер, Whisper на CUDA, аппаратный энкодер NVENC и веса YOLOv8
            </p>
          </div>
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-xs font-semibold text-white shadow-lg shadow-accent-600/20"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Сохранить настройки
          </button>
        </div>

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
      </div>
    </div>
  );
}
