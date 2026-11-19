import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, EyeOff, Pause, Play, ShieldAlert } from "lucide-react";
import { api } from "../lib/api";
import type {
  AdPreset,
  BackgroundAsset,
  Clip,
  Project,
  SubtitlePreset,
} from "../types/api";

interface Props {
  project: Project;
  clip: Clip;
  subtitlePreset: SubtitlePreset | null;
  adPreset: AdPreset | null;
  backgroundAsset: BackgroundAsset | null;
  onTimeChange?: (globalTime: number) => void;
}

export function VideoPreview916({
  project,
  clip,
  subtitlePreset,
  adPreset,
  backgroundAsset,
  onTimeChange,
}: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const bgVideoRef = useRef<HTMLVideoElement | null>(null);
  const blurBgVideoRef = useRef<HTMLVideoElement | null>(null);

  const [useRendered, setUseRendered] = useState<boolean>(Boolean(clip.preview_path || clip.export_path));
  const [showSafeZone, setShowSafeZone] = useState<boolean>(true);
  const [playing, setPlaying] = useState<boolean>(false);
  const [relTime, setRelTime] = useState<number>(0);

  const clipDuration = Math.max(0.2, clip.end_time - clip.start_time);
  const renderedPath = clip.export_path || clip.preview_path;
  const proxySrc = api.mediaUrl(project.proxy_path || project.source_path);
  const renderedSrc = api.mediaUrl(renderedPath);

  useEffect(() => {
    setUseRendered(Boolean(clip.preview_path || clip.export_path));
    setRelTime(0);
    setPlaying(false);
  }, [clip.id, clip.preview_path, clip.export_path]);

  // Sync proxy playback bounds [clip.start_time, clip.end_time]
  useEffect(() => {
    const v = videoRef.current;
    if (!v || useRendered) return;
    v.currentTime = clip.start_time;
    if (blurBgVideoRef.current) {
      blurBgVideoRef.current.currentTime = clip.start_time;
    }
    setRelTime(0);
  }, [clip.id, clip.start_time, useRendered]);

  const handleTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    if (useRendered) {
      setRelTime(v.currentTime);
      onTimeChange?.(clip.start_time + v.currentTime);
      return;
    }
    const rel = Math.max(0, v.currentTime - clip.start_time);
    setRelTime(rel);
    onTimeChange?.(v.currentTime);
    if (v.currentTime >= clip.end_time) {
      v.currentTime = clip.start_time;
      if (blurBgVideoRef.current) {
        blurBgVideoRef.current.currentTime = clip.start_time;
      }
      if (!v.paused) {
        void v.play();
        blurBgVideoRef.current?.play().catch(() => {});
      }
    }
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      if (!useRendered && (v.currentTime < clip.start_time || v.currentTime >= clip.end_time)) {
        v.currentTime = clip.start_time;
        if (blurBgVideoRef.current) {
          blurBgVideoRef.current.currentTime = clip.start_time;
        }
      }
      void v.play();
      bgVideoRef.current?.play().catch(() => {});
      blurBgVideoRef.current?.play().catch(() => {});
      setPlaying(true);
    } else {
      v.pause();
      bgVideoRef.current?.pause();
      blurBgVideoRef.current?.pause();
      setPlaying(false);
    }
  };

  const handleScrub = (nextRel: number) => {
    const clamped = Math.max(0, Math.min(clipDuration, nextRel));
    setRelTime(clamped);
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = useRendered ? clamped : clip.start_time + clamped;
    if (blurBgVideoRef.current && !useRendered) {
      blurBgVideoRef.current.currentTime = clip.start_time + clamped;
    }
    onTimeChange?.(clip.start_time + clamped);
  };

  // Active track center for live auto_track preview
  const trackCx = useMemo(() => {
    const pts = clip.track_data_json?.points;
    if (!pts || pts.length === 0) return 0.5;
    let best = pts[0];
    let bestDiff = Math.abs(pts[0].t - relTime);
    for (const pt of pts) {
      const diff = Math.abs(pt.t - relTime);
      if (diff < bestDiff) {
        best = pt;
        bestDiff = diff;
      }
    }
    return best.cx;
  }, [clip.track_data_json, relTime]);

  const activeCue = useMemo(() => {
    if (!clip.subtitles_enabled) return null;
    return (
      clip.subtitles_json.find((c) => relTime >= c.start && relTime <= c.end) ?? null
    );
  }, [clip.subtitles_enabled, clip.subtitles_json, relTime]);

  const showHook = clip.hook_enabled && clip.hook_text.trim().length > 0 && relTime <= 3.2;

  const adOverlayProps = useMemo(() => {
    if (!adPreset || adPreset.ad_type !== "overlay") return null;
    const ov = clip.ad_override_json ?? {};
    const start = ov.overlay_start ?? adPreset.overlay_start;
    const end = ov.overlay_end ?? adPreset.overlay_end;
    if (relTime < start || relTime > end) return null;
    return {
      x: ov.overlay_x ?? adPreset.overlay_x,
      y: ov.overlay_y ?? adPreset.overlay_y,
      scale: ov.overlay_scale ?? adPreset.overlay_scale,
      src: api.mediaUrl(adPreset.asset_path),
      isVideo: adPreset.asset_path.toLowerCase().endsWith(".webm"),
    };
  }, [adPreset, clip.ad_override_json, relTime]);

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="flex items-center justify-between w-full max-w-[300px] text-[11px]">
        <div className="inline-flex rounded-lg bg-surface-900 border border-surface-700 p-0.5">
          <button
            onClick={() => setUseRendered(false)}
            className={`px-2.5 py-1 rounded-md transition-colors ${
              !useRendered ? "bg-accent-600 text-white" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Прокси
          </button>
          <button
            onClick={() => renderedPath && setUseRendered(true)}
            disabled={!renderedPath}
            className={`px-2.5 py-1 rounded-md transition-colors disabled:opacity-40 ${
              useRendered ? "bg-accent-600 text-white" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Рендер
          </button>
        </div>

        <button
          onClick={() => setShowSafeZone(!showSafeZone)}
          className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg border text-[11px] ${
            showSafeZone
              ? "bg-amber-500/10 border-amber-500/30 text-amber-300"
              : "bg-surface-900 border-surface-700 text-zinc-400"
          }`}
          title="Показать границы интерфейса TikTok / Shorts / Reels"
        >
          {showSafeZone ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
          Safe-Zone
        </button>
      </div>

      {/* 9:16 Viewport */}
      <div className="relative w-[270px] h-[480px] rounded-2xl overflow-hidden bg-black border-2 border-surface-700 shadow-2xl">
        {useRendered && renderedSrc ? (
          <video
            ref={videoRef}
            src={renderedSrc}
            onTimeUpdate={handleTimeUpdate}
            onEnded={() => setPlaying(false)}
            className="w-full h-full object-contain bg-black"
            playsInline
          />
        ) : (
          <div className="relative w-full h-full overflow-hidden">
            {clip.crop_mode === "split_screen" ? (
              <div className="w-full h-full flex flex-col">
                <div className="w-full h-1/2 overflow-hidden bg-black">
                  <video
                    ref={videoRef}
                    src={proxySrc}
                    onTimeUpdate={handleTimeUpdate}
                    className="w-full h-full object-cover"
                    playsInline
                  />
                </div>
                <div className="w-full h-1/2 overflow-hidden bg-surface-950 relative">
                  {backgroundAsset ? (
                    <video
                      ref={bgVideoRef}
                      src={api.mediaUrl(backgroundAsset.file_path)}
                      muted
                      loop
                      playsInline
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-[11px] text-zinc-500 px-4 text-center">
                      Выберите фоновое видео справа
                    </div>
                  )}
                </div>
              </div>
            ) : clip.crop_mode === "cinema_blur" ? (
              <div className="relative w-full h-full flex items-center justify-center bg-surface-950">
                <video
                  ref={blurBgVideoRef}
                  src={proxySrc}
                  muted
                  className="absolute inset-0 w-full h-full object-cover blur-xl scale-110 opacity-45 pointer-events-none"
                  playsInline
                />
                <video
                  ref={videoRef}
                  src={proxySrc}
                  onTimeUpdate={handleTimeUpdate}
                  className="relative z-10 w-full max-h-full object-contain"
                  playsInline
                />
              </div>
            ) : (
              <video
                ref={videoRef}
                src={proxySrc}
                onTimeUpdate={handleTimeUpdate}
                className="w-full h-full object-cover transition-[object-position] duration-150"
                style={{ objectPosition: `${Math.round(trackCx * 100)}% 50%` }}
                playsInline
              />
            )}

            {/* Simulated 3s Hook */}
            {showHook && (
              <div
                className="absolute top-10 left-4 right-4 z-20 px-2.5 py-1.5 rounded-lg text-center font-extrabold text-xs uppercase shadow-lg"
                style={{
                  backgroundColor: subtitlePreset?.hook_bg_color || "#7C3AED",
                  color: subtitlePreset?.hook_text_color || "#FFFFFF",
                }}
              >
                {clip.hook_text}
              </div>
            )}

            {/* Simulated Subtitles */}
            {activeCue && (
              <div
                className="absolute left-4 right-10 z-20 text-center pointer-events-none"
                style={{
                  bottom: `${Math.round(((subtitlePreset?.margin_bottom ?? 380) / 1920) * 100)}%`,
                }}
              >
                <span
                  className="inline-block font-extrabold text-sm leading-snug px-1.5 py-0.5 drop-shadow-[0_2px_4px_rgba(0,0,0,0.95)]"
                  style={{
                    color: subtitlePreset?.highlight_color || "#FFE600",
                    textTransform: subtitlePreset?.uppercase ? "uppercase" : "none",
                  }}
                >
                  {activeCue.text}
                </span>
              </div>
            )}

            {/* Simulated Mode 2 Ad Overlay */}
            {adOverlayProps && (
              <div
                className="absolute z-20 pointer-events-none -translate-x-1/2 -translate-y-1/2"
                style={{
                  left: `${adOverlayProps.x}%`,
                  top: `${adOverlayProps.y}%`,
                  width: `${adOverlayProps.scale}%`,
                }}
              >
                {adOverlayProps.isVideo ? (
                  <video
                    src={adOverlayProps.src}
                    autoPlay
                    muted
                    loop
                    playsInline
                    className="w-full h-auto rounded"
                  />
                ) : (
                  <img
                    src={adOverlayProps.src}
                    alt="Ad overlay"
                    className="w-full h-auto object-contain"
                  />
                )}
              </div>
            )}
          </div>
        )}

        {/* TikTok / Shorts Safe-Zone Guide */}
        {showSafeZone && (
          <div className="absolute inset-0 z-30 pointer-events-none">
            <div className="absolute top-0 left-0 right-0 h-[10%] border-b border-dashed border-amber-400/40 bg-amber-400/5 flex items-center justify-center text-[9px] text-amber-300/70">
              Верхний UI (поиск / статус)
            </div>
            <div className="absolute right-0 top-[42%] bottom-[22%] w-[15%] border-l border-dashed border-amber-400/40 bg-amber-400/5 flex items-center justify-center">
              <span className="-rotate-90 text-[9px] text-amber-300/70 whitespace-nowrap">
                Кнопки UI
              </span>
            </div>
            <div className="absolute bottom-0 left-0 right-0 h-[22%] border-t border-dashed border-amber-400/50 bg-amber-400/5 flex items-end justify-center pb-1.5 text-[9px] text-amber-300/80 gap-1">
              <ShieldAlert className="w-3 h-3" />
              <span>Зона описания и звука (не перекрывать)</span>
            </div>
          </div>
        )}
      </div>

      {/* Transport controls */}
      <div className="w-full max-w-[300px] flex items-center gap-2.5 bg-surface-900 border border-surface-800 rounded-xl px-3 py-2">
        <button
          onClick={togglePlay}
          className="w-7 h-7 rounded-lg bg-accent-600 hover:bg-accent-500 text-white flex items-center justify-center shrink-0"
        >
          {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
        </button>
        <input
          type="range"
          min={0}
          max={clipDuration}
          step={0.05}
          value={Math.min(clipDuration, relTime)}
          onChange={(e) => handleScrub(Number(e.target.value))}
          className="flex-1 accent-accent-500"
        />
        <span className="text-[11px] font-mono text-zinc-400 tabular-nums shrink-0">
          {relTime.toFixed(1)}s / {clipDuration.toFixed(1)}s
        </span>
      </div>
    </div>
  );
}
