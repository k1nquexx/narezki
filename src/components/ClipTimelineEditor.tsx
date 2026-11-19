import { useMemo } from "react";
import { Magnet } from "lucide-react";
import type { AdPreset, Clip, Project } from "../types/api";

interface Props {
  project: Project;
  clip: Clip;
  adPreset: AdPreset | null;
  onChangeRange: (start: number, end: number) => void;
}

export function ClipTimelineEditor({ project, clip, adPreset, onChangeRange }: Props) {
  const duration = Math.max(1.0, project.duration || clip.end_time);
  const scenes = project.analysis_json?.scenes ?? [];
  const rawRmsDb = project.analysis_json?.audio_peaks?.rms_db;

  const sparkBars = useMemo(() => {
    const rmsDb = rawRmsDb ?? [];
    if (rmsDb.length === 0) return [];
    const bucketCount = 90;
    const step = Math.max(1, Math.floor(rmsDb.length / bucketCount));
    const bars: number[] = [];
    for (let i = 0; i < rmsDb.length; i += step) {
      const slice = rmsDb.slice(i, i + step);
      const peak = Math.max(...slice);
      // Normalize -60..0 dB to 10..100%
      const norm = Math.max(8, Math.min(100, ((peak + 60) / 60) * 100));
      bars.push(norm);
    }
    return bars;
  }, [rawRmsDb]);

  const startPct = (clip.start_time / duration) * 100;
  const endPct = (clip.end_time / duration) * 100;
  const clipDur = Math.max(0.5, clip.end_time - clip.start_time);

  const isMidRoll =
    adPreset &&
    adPreset.ad_type === "video_insert" &&
    (clip.ad_override_json?.insert_position ?? adPreset.insert_position) === "mid_roll";
  const midRollAt =
    clip.ad_override_json?.insert_at_sec ?? adPreset?.insert_at_sec ?? clipDur * 0.5;
  const midRollGlobalPct =
    ((clip.start_time + Math.min(clipDur, Math.max(0, midRollAt))) / duration) * 100;

  const snapToNearestCut = () => {
    if (scenes.length === 0) return;
    const nearestStart = scenes.reduce(
      (best, c) => (Math.abs(c - clip.start_time) < Math.abs(best - clip.start_time) ? c : best),
      clip.start_time
    );
    const nearestEnd = scenes.reduce(
      (best, c) => (Math.abs(c - clip.end_time) < Math.abs(best - clip.end_time) ? c : best),
      clip.end_time
    );
    const nextStart =
      Math.abs(nearestStart - clip.start_time) <= 3.0 ? nearestStart : clip.start_time;
    const nextEnd =
      Math.abs(nearestEnd - clip.end_time) <= 3.0 ? nearestEnd : clip.end_time;
    if (nextEnd - nextStart >= 3.0) {
      onChangeRange(Number(nextStart.toFixed(2)), Number(nextEnd.toFixed(2)));
    }
  };

  return (
    <div className="rounded-xl border border-surface-800 bg-surface-900 p-3.5 space-y-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-zinc-200">
          Таймлайн клипа ({clip.start_time.toFixed(2)}с – {clip.end_time.toFixed(2)}с ·{" "}
          {clipDur.toFixed(1)}с)
        </span>
        {scenes.length > 0 && (
          <button
            onClick={snapToNearestCut}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-[11px] text-sky-300"
          >
            <Magnet className="w-3 h-3" />
            Привязать к склейкам
          </button>
        )}
      </div>

      {/* Waveform + Scene Cuts + Selected Window */}
      <div className="relative h-14 rounded-lg bg-surface-950 border border-surface-800 overflow-hidden flex items-end px-0.5 gap-[1px]">
        {sparkBars.length > 0 ? (
          sparkBars.map((h, i) => (
            <div
              key={i}
              className="flex-1 bg-zinc-700/70 rounded-t-sm"
              style={{ height: `${h}%` }}
            />
          ))
        ) : (
          <div className="m-auto text-[10px] text-zinc-600">
            Спектр аудио появится после AI-анализа
          </div>
        )}

        {/* Selected region highlight */}
        <div
          className="absolute top-0 bottom-0 bg-accent-500/20 border-x-2 border-accent-400 pointer-events-none"
          style={{
            left: `${Math.max(0, startPct)}%`,
            width: `${Math.max(1, endPct - startPct)}%`,
          }}
        />

        {/* Scene cut markers */}
        {scenes.slice(0, 120).map((cut, idx) => (
          <div
            key={idx}
            className="absolute top-0 bottom-0 w-[1px] bg-sky-400/50 pointer-events-none"
            style={{ left: `${(cut / duration) * 100}%` }}
            title={`Склейка: ${cut.toFixed(2)}s`}
          />
        ))}

        {/* Mid-roll ad marker */}
        {isMidRoll && (
          <div
            className="absolute top-0 bottom-0 w-[2px] bg-amber-400 pointer-events-none"
            style={{ left: `${midRollGlobalPct}%` }}
            title={`Реклама mid-roll: +${midRollAt.toFixed(1)}s`}
          />
        )}
      </div>

      {/* Numeric Start / End fine controls */}
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-zinc-400 w-12 shrink-0">Старт</span>
          <button
            onClick={() => onChangeRange(Math.max(0, Number((clip.start_time - 0.5).toFixed(2))), clip.end_time)}
            className="px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 text-zinc-300"
          >
            -0.5
          </button>
          <input
            type="number"
            step="0.1"
            min={0}
            max={Math.max(0, clip.end_time - 0.5)}
            value={clip.start_time}
            onChange={(e) => {
              const v = Math.max(0, Math.min(clip.end_time - 0.5, Number(e.target.value)));
              onChangeRange(Number(v.toFixed(2)), clip.end_time);
            }}
            className="w-full px-2 py-1 rounded bg-surface-950 border border-surface-700 text-zinc-100 text-center font-mono"
          />
          <button
            onClick={() =>
              onChangeRange(
                Math.min(clip.end_time - 0.5, Number((clip.start_time + 0.5).toFixed(2))),
                clip.end_time
              )
            }
            className="px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 text-zinc-300"
          >
            +0.5
          </button>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-zinc-400 w-12 shrink-0">Конец</span>
          <button
            onClick={() =>
              onChangeRange(
                clip.start_time,
                Math.max(clip.start_time + 0.5, Number((clip.end_time - 0.5).toFixed(2)))
              )
            }
            className="px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 text-zinc-300"
          >
            -0.5
          </button>
          <input
            type="number"
            step="0.1"
            min={clip.start_time + 0.5}
            max={duration}
            value={clip.end_time}
            onChange={(e) => {
              const v = Math.max(clip.start_time + 0.5, Math.min(duration, Number(e.target.value)));
              onChangeRange(clip.start_time, Number(v.toFixed(2)));
            }}
            className="w-full px-2 py-1 rounded bg-surface-950 border border-surface-700 text-zinc-100 text-center font-mono"
          />
          <button
            onClick={() =>
              onChangeRange(
                clip.start_time,
                Math.min(duration, Number((clip.end_time + 0.5).toFixed(2)))
              )
            }
            className="px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 text-zinc-300"
          >
            +0.5
          </button>
        </div>
      </div>
    </div>
  );
}
