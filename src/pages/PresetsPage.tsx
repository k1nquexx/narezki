import { useState } from "react";
import { FolderOpen, Megaphone, Plus, Subtitles, Trash2, Upload, Video } from "lucide-react";
import { api } from "../lib/api";
import { isTauriRuntime, pickAdAssetFile, pickDirectory, pickVideoFile } from "../lib/tauri";
import { useAppStore } from "../store/useAppStore";
import type { AdInsertPosition, AdType, KaraokeMode } from "../types/api";

type PresetTab = "ads" | "subtitles" | "backgrounds";

export function PresetsPage() {
  const subtitlePresets = useAppStore((s) => s.subtitlePresets);
  const adPresets = useAppStore((s) => s.adPresets);
  const backgrounds = useAppStore((s) => s.backgrounds);
  const refreshPresets = useAppStore((s) => s.refreshPresets);
  const addToast = useAppStore((s) => s.addToast);

  const [tab, setTab] = useState<PresetTab>("ads");

  // Ad creation state
  const [adName, setAdName] = useState("");
  const [adType, setAdType] = useState<AdType>("overlay");
  const [adPath, setAdPath] = useState("");
  const [adPos, setAdPos] = useState<AdInsertPosition>("outro");
  const [adInsertAt, setAdInsertAt] = useState<number>(8);
  const [ovX, setOvX] = useState(50);
  const [ovY, setOvY] = useState(22);
  const [ovScale, setOvScale] = useState(35);
  const [ovStart, setOvStart] = useState(3);
  const [ovEnd, setOvEnd] = useState(12);
  const [ovFade, setOvFade] = useState(0.4);

  // Subtitle creation state
  const [subName, setSubName] = useState("");
  const [fontName, setFontName] = useState("Arial");
  const [fontSize, setFontSize] = useState(68);
  const [primaryColor, setPrimaryColor] = useState("#FFFFFF");
  const [highlightColor, setHighlightColor] = useState("#FFE600");
  const [outlineColor, setOutlineColor] = useState("#000000");
  const [marginBottom, setMarginBottom] = useState(380);
  const [wordsPerLine, setWordsPerLine] = useState(3);
  const [karaokeMode, setKaraokeMode] = useState<KaraokeMode>("karaoke_fill");
  const [uppercase, setUppercase] = useState(true);

  // Background state
  const [bgPath, setBgPath] = useState("");
  const [bgFolder, setBgFolder] = useState("");

  const handleBrowseAd = async () => {
    const file = await pickAdAssetFile(adType);
    if (file) setAdPath(file);
  };

  const handleCreateAd = async () => {
    if (!adName.trim() || !adPath.trim()) {
      addToast({ type: "warning", title: "Укажите название и файл рекламы" });
      return;
    }
    try {
      await api.createAdPreset({
        name: adName.trim(),
        ad_type: adType,
        asset_path: adPath.trim(),
        insert_position: adPos,
        insert_at_sec: adPos === "mid_roll" ? adInsertAt : null,
        overlay_x: ovX,
        overlay_y: ovY,
        overlay_scale: ovScale,
        overlay_start: ovStart,
        overlay_end: ovEnd,
        fade_duration: ovFade,
      });
      setAdName("");
      setAdPath("");
      await refreshPresets();
      addToast({ type: "success", title: "Рекламный пресет создан" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка создания рекламы",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleUploadAdFile = async (file: File) => {
    const fd = new FormData();
    fd.append("name", adName.trim() || file.name);
    fd.append("ad_type", adType);
    fd.append("insert_position", adPos);
    if (adPos === "mid_roll") fd.append("insert_at_sec", String(adInsertAt));
    fd.append("overlay_x", String(ovX));
    fd.append("overlay_y", String(ovY));
    fd.append("overlay_scale", String(ovScale));
    fd.append("overlay_start", String(ovStart));
    fd.append("overlay_end", String(ovEnd));
    fd.append("fade_duration", String(ovFade));
    fd.append("file", file);
    try {
      await api.uploadAdPreset(fd);
      setAdName("");
      await refreshPresets();
      addToast({ type: "success", title: "Реклама загружена" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка загрузки",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleCreateSubtitlePreset = async () => {
    if (!subName.trim()) {
      addToast({ type: "warning", title: "Введите название пресета субтитров" });
      return;
    }
    try {
      await api.createSubtitlePreset({
        name: subName.trim(),
        font_name: fontName,
        font_size: fontSize,
        primary_color: primaryColor,
        highlight_color: highlightColor,
        outline_color: outlineColor,
        outline_width: 5,
        shadow_depth: 3,
        bold: true,
        uppercase,
        margin_bottom: marginBottom,
        words_per_line: wordsPerLine,
        karaoke_mode: karaokeMode,
        hook_bg_color: "#7C3AED",
        hook_text_color: "#FFFFFF",
        is_default: false,
      });
      setSubName("");
      await refreshPresets();
      addToast({ type: "success", title: "Стиль субтитров добавлен" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка сохранения стиля",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleAddBackground = async (pathOverride?: string) => {
    const p = (pathOverride ?? bgPath).trim();
    if (!p) return;
    try {
      await api.addBackground(p);
      setBgPath("");
      await refreshPresets();
      addToast({ type: "success", title: "Фоновое видео добавлено" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка добавления фона",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleScanBgFolder = async (folderOverride?: string) => {
    const folder = (folderOverride ?? bgFolder).trim();
    if (!folder) return;
    try {
      const added = await api.scanBackgroundFolder(folder);
      await refreshPresets();
      addToast({
        type: "success",
        title: `Добавлено фонов: ${added.length}`,
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка сканирования папки",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleDeleteWithToast = async (title: string, action: () => Promise<unknown>) => {
    try {
      await action();
      await refreshPresets();
    } catch (err) {
      addToast({
        type: "error",
        title,
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Реклама, субтитры и фоны</h1>
          <p className="text-xs text-zinc-400 mt-1">
            Управление пресетами микрорекламы, стилями .ass-субтитров и залипательными фонами для Split Screen
          </p>
        </div>
        <div className="inline-flex rounded-lg bg-surface-900 border border-surface-800 p-1 text-xs">
          <button
            onClick={() => setTab("ads")}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md ${
              tab === "ads" ? "bg-accent-600 text-white" : "text-zinc-400 hover:text-white"
            }`}
          >
            <Megaphone className="w-3.5 h-3.5" />
            Реклама ({adPresets.length})
          </button>
          <button
            onClick={() => setTab("subtitles")}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md ${
              tab === "subtitles" ? "bg-accent-600 text-white" : "text-zinc-400 hover:text-white"
            }`}
          >
            <Subtitles className="w-3.5 h-3.5" />
            Субтитры ({subtitlePresets.length})
          </button>
          <button
            onClick={() => setTab("backgrounds")}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md ${
              tab === "backgrounds" ? "bg-accent-600 text-white" : "text-zinc-400 hover:text-white"
            }`}
          >
            <Video className="w-3.5 h-3.5" />
            Фоны Split-Screen ({backgrounds.length})
          </button>
        </div>
      </div>

      {tab === "ads" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-3 text-xs">
            <h2 className="text-sm font-semibold text-white">Новый пресет рекламы</h2>
            <input
              value={adName}
              onChange={(e) => setAdName(e.target.value)}
              placeholder="Название (например: ТГ-канал промокод)"
              className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
            />
            <select
              value={adType}
              onChange={(e) => setAdType(e.target.value as AdType)}
              className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
            >
              <option value="overlay">Режим 2: Статичный/аним. оверлей (PNG/WebM/GIF)</option>
              <option value="video_insert">Режим 1: Видеовклейка (Mid-roll / Outro)</option>
            </select>

            <div className="flex gap-2">
              <input
                value={adPath}
                onChange={(e) => setAdPath(e.target.value)}
                placeholder="Путь к файлу рекламы..."
                className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              />
              {isTauriRuntime() && (
                <button
                  onClick={handleBrowseAd}
                  className="px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-zinc-200"
                >
                  <FolderOpen className="w-4 h-4" />
                </button>
              )}
            </div>

            <label className="inline-flex items-center gap-1.5 text-zinc-400 cursor-pointer hover:text-zinc-200">
              <Upload className="w-3.5 h-3.5" />
              <span>Или загрузить файл через форму...</span>
              <input
                type="file"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUploadAdFile(f);
                }}
              />
            </label>

            {adType === "video_insert" ? (
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={adPos}
                  onChange={(e) => setAdPos(e.target.value as AdInsertPosition)}
                  className="px-2.5 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                >
                  <option value="outro">Outro (в конце)</option>
                  <option value="mid_roll">Mid-roll (в центре)</option>
                </select>
                {adPos === "mid_roll" && (
                  <input
                    type="number"
                    value={adInsertAt}
                    onChange={(e) => setAdInsertAt(Number(e.target.value))}
                    placeholder="Секунда"
                    className="px-2.5 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                  />
                )}
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-2">
                <label className="space-y-1">
                  <span className="text-zinc-400">X (%)</span>
                  <input
                    type="number"
                    value={ovX}
                    onChange={(e) => setOvX(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-zinc-400">Y (%)</span>
                  <input
                    type="number"
                    value={ovY}
                    onChange={(e) => setOvY(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-zinc-400">Масштаб %</span>
                  <input
                    type="number"
                    value={ovScale}
                    onChange={(e) => setOvScale(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-zinc-400">Старт (с)</span>
                  <input
                    type="number"
                    value={ovStart}
                    onChange={(e) => setOvStart(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-zinc-400">Конец (с)</span>
                  <input
                    type="number"
                    value={ovEnd}
                    onChange={(e) => setOvEnd(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-zinc-400">Fade (с)</span>
                  <input
                    type="number"
                    step="0.1"
                    value={ovFade}
                    onChange={(e) => setOvFade(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                  />
                </label>
              </div>
            )}

            <button
              onClick={handleCreateAd}
              className="w-full py-2 rounded-lg bg-accent-600 hover:bg-accent-500 font-semibold text-white inline-flex items-center justify-center gap-1.5"
            >
              <Plus className="w-4 h-4" />
              Создать пресет рекламы
            </button>
          </div>

          <div className="lg:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-3 content-start">
            {adPresets.length === 0 ? (
              <div className="md:col-span-2 rounded-xl border border-dashed border-surface-700 p-10 text-center text-xs text-zinc-500">
                Пресеты рекламы пока не созданы.
              </div>
            ) : (
              adPresets.map((a) => (
                <div
                  key={a.id}
                  className="rounded-xl border border-surface-800 bg-surface-900 p-3.5 flex items-start justify-between gap-3"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="text-sm font-semibold text-zinc-100 truncate">{a.name}</div>
                    <div className="text-[11px] text-accent-300">
                      {a.ad_type === "video_insert"
                        ? `Видеовклейка · ${a.insert_position} (${a.duration.toFixed(1)}с)`
                        : `Оверлей · X:${a.overlay_x}% Y:${a.overlay_y}% · ${a.overlay_start}–${a.overlay_end}с`}
                    </div>
                    <div className="text-[10px] text-zinc-500 truncate">{a.asset_path}</div>
                  </div>
                  <button
                    onClick={() =>
                      void handleDeleteWithToast("Ошибка удаления рекламы", () =>
                        api.deleteAdPreset(a.id)
                      )
                    }
                    className="text-zinc-500 hover:text-rose-400"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {tab === "subtitles" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-3 text-xs">
            <h2 className="text-sm font-semibold text-white">Новый стиль субтитров</h2>
            <input
              value={subName}
              onChange={(e) => setSubName(e.target.value)}
              placeholder="Название стиля"
              className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
            />
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1">
                <span className="text-zinc-400">Шрифт</span>
                <input
                  value={fontName}
                  onChange={(e) => setFontName(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded bg-surface-950 border border-surface-700"
                />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Размер (px)</span>
                <input
                  type="number"
                  value={fontSize}
                  onChange={(e) => setFontSize(Number(e.target.value))}
                  className="w-full px-2.5 py-1.5 rounded bg-surface-950 border border-surface-700"
                />
              </label>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <label className="space-y-1">
                <span className="text-zinc-400">Основной</span>
                <input
                  type="color"
                  value={primaryColor}
                  onChange={(e) => setPrimaryColor(e.target.value)}
                  className="w-full h-8 rounded bg-surface-950 border border-surface-700"
                />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Активное слово</span>
                <input
                  type="color"
                  value={highlightColor}
                  onChange={(e) => setHighlightColor(e.target.value)}
                  className="w-full h-8 rounded bg-surface-950 border border-surface-700"
                />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Обводка</span>
                <input
                  type="color"
                  value={outlineColor}
                  onChange={(e) => setOutlineColor(e.target.value)}
                  className="w-full h-8 rounded bg-surface-950 border border-surface-700"
                />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1">
                <span className="text-zinc-400">Режим караоке</span>
                <select
                  value={karaokeMode}
                  onChange={(e) => setKaraokeMode(e.target.value as KaraokeMode)}
                  className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                >
                  <option value="karaoke_fill">Караоке (\kf)</option>
                  <option value="word_highlight">Подсветка слова</option>
                  <option value="phrase">Целая фраза</option>
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Отступ снизу (px)</span>
                <input
                  type="number"
                  value={marginBottom}
                  onChange={(e) => setMarginBottom(Number(e.target.value))}
                  className="w-full px-2 py-1.5 rounded bg-surface-950 border border-surface-700"
                />
              </label>
            </div>
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-zinc-300">
                <input
                  type="checkbox"
                  checked={uppercase}
                  onChange={(e) => setUppercase(e.target.checked)}
                  className="accent-accent-500"
                />
                CAPS LOCK
              </label>
              <label className="flex items-center gap-2 text-zinc-300">
                Слов в строке:
                <input
                  type="number"
                  min={1}
                  max={6}
                  value={wordsPerLine}
                  onChange={(e) => setWordsPerLine(Number(e.target.value))}
                  className="w-12 px-1.5 py-0.5 rounded bg-surface-950 border border-surface-700"
                />
              </label>
            </div>

            {/* Live mini preview */}
            <div className="p-4 rounded-lg bg-surface-950 border border-surface-800 text-center">
              <span
                className="font-extrabold text-base tracking-wide"
                style={{
                  color: primaryColor,
                  textShadow: `0 2px 0 ${outlineColor}, -1px -1px 0 ${outlineColor}, 1px -1px 0 ${outlineColor}`,
                }}
              >
                {uppercase ? "ПРИМЕР " : "Пример "}
              </span>
              <span
                className="font-extrabold text-base tracking-wide"
                style={{
                  color: highlightColor,
                  textShadow: `0 2px 0 ${outlineColor}, -1px -1px 0 ${outlineColor}, 1px -1px 0 ${outlineColor}`,
                }}
              >
                {uppercase ? "СУБТИТРОВ" : "субтитров"}
              </span>
            </div>

            <button
              onClick={handleCreateSubtitlePreset}
              className="w-full py-2 rounded-lg bg-accent-600 hover:bg-accent-500 font-semibold text-white"
            >
              Сохранить стиль
            </button>
          </div>

          <div className="lg:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-3 content-start">
            {subtitlePresets.map((p) => (
              <div
                key={p.id}
                className="rounded-xl border border-surface-800 bg-surface-900 p-4 flex items-center justify-between"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-white">{p.name}</span>
                    {p.is_default && (
                      <span className="px-1.5 py-0.5 rounded bg-accent-600/20 text-accent-300 text-[10px]">
                        По умолчанию
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-zinc-400">
                    {p.font_name} · {p.font_size}px · {p.karaoke_mode} · отступ {p.margin_bottom}px
                  </div>
                  <div className="flex items-center gap-2 pt-1">
                    <span
                      className="w-4 h-4 rounded-full border border-white/20"
                      style={{ backgroundColor: p.primary_color }}
                    />
                    <span
                      className="w-4 h-4 rounded-full border border-white/20"
                      style={{ backgroundColor: p.highlight_color }}
                    />
                  </div>
                </div>
                {!p.is_default && (
                  <button
                    onClick={() =>
                      void handleDeleteWithToast("Ошибка удаления стиля субтитров", () =>
                        api.deleteSubtitlePreset(p.id)
                      )
                    }
                    className="text-zinc-500 hover:text-rose-400"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "backgrounds" && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
            <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-2.5">
              <div className="font-semibold text-white">Добавить один фоновый ролик</div>
              <div className="flex gap-2">
                <input
                  value={bgPath}
                  onChange={(e) => setBgPath(e.target.value)}
                  placeholder="Путь к видео (Minecraft parkour, GTA, ASMR...)"
                  className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
                {isTauriRuntime() && (
                  <button
                    onClick={async () => {
                      const f = await pickVideoFile();
                      if (f) await handleAddBackground(f);
                    }}
                    className="px-3 py-2 rounded-lg bg-surface-800 border border-surface-700"
                  >
                    <FolderOpen className="w-4 h-4" />
                  </button>
                )}
                <button
                  onClick={() => void handleAddBackground()}
                  className="px-3 py-2 rounded-lg bg-accent-600 text-white font-medium"
                >
                  Добавить
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-2.5">
              <div className="font-semibold text-white">Импортировать папку с фонами</div>
              <div className="flex gap-2">
                <input
                  value={bgFolder}
                  onChange={(e) => setBgFolder(e.target.value)}
                  placeholder="Папка с видео..."
                  className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
                {isTauriRuntime() && (
                  <button
                    onClick={async () => {
                      const d = await pickDirectory("Папка с фоновыми видео");
                      if (d) {
                        setBgFolder(d);
                        await handleScanBgFolder(d);
                      }
                    }}
                    className="px-3 py-2 rounded-lg bg-surface-800 border border-surface-700"
                  >
                    <FolderOpen className="w-4 h-4" />
                  </button>
                )}
                <button
                  onClick={() => void handleScanBgFolder()}
                  className="px-3 py-2 rounded-lg bg-accent-600 text-white font-medium"
                >
                  Сканировать
                </button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3.5">
            {backgrounds.map((b) => (
              <div
                key={b.id}
                className="rounded-xl border border-surface-800 bg-surface-900 overflow-hidden"
              >
                <div className="aspect-video bg-surface-950">
                  {b.thumbnail_path && (
                    <img
                      src={api.mediaUrl(b.thumbnail_path)}
                      alt={b.name}
                      className="w-full h-full object-cover"
                    />
                  )}
                </div>
                <div className="p-2.5 flex items-center justify-between gap-2 text-xs">
                  <div className="min-w-0">
                    <div className="font-medium text-zinc-100 truncate">{b.name}</div>
                    <div className="text-[10px] text-zinc-400">
                      {b.width}×{b.height} · {b.duration.toFixed(1)}с
                    </div>
                  </div>
                  <button
                    onClick={() =>
                      void handleDeleteWithToast("Ошибка удаления фона", () =>
                        api.deleteBackground(b.id)
                      )
                    }
                    className="text-zinc-500 hover:text-rose-400"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
