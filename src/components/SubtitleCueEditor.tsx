import { Plus, RefreshCw, Trash2 } from "lucide-react";
import type { SubtitleCue, WordTiming } from "../types/api";

interface Props {
  cues: SubtitleCue[];
  clipDuration: number;
  onChange: (nextCues: SubtitleCue[]) => void;
  onResync: () => void;
  resyncing?: boolean;
}

function rebuildWordTimings(text: string, start: number, end: number): WordTiming[] {
  const parts = text
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (parts.length === 0) return [];
  const dur = Math.max(0.1, end - start);
  const step = dur / parts.length;
  return parts.map((word, idx) => ({
    word,
    start: Number((start + idx * step).toFixed(3)),
    end: Number((start + (idx + 1) * step).toFixed(3)),
  }));
}

export function SubtitleCueEditor({
  cues,
  clipDuration,
  onChange,
  onResync,
  resyncing = false,
}: Props) {
  const updateCue = (idx: number, patch: Partial<SubtitleCue>) => {
    const next = cues.map((c, i) => {
      if (i !== idx) return c;
      const merged = { ...c, ...patch };
      if (patch.text !== undefined || patch.start !== undefined || patch.end !== undefined) {
        merged.words = rebuildWordTimings(merged.text, merged.start, merged.end);
      }
      return merged;
    });
    onChange(next);
  };

  const addCue = () => {
    const lastEnd = cues.length > 0 ? cues[cues.length - 1].end : 0;
    const start = Math.min(Math.max(0, lastEnd + 0.1), Math.max(0, clipDuration - 0.5));
    const end = Math.min(clipDuration, start + 1.5);
    const text = "Новый субтитр";
    onChange([
      ...cues,
      {
        id: `cue_${Date.now()}`,
        start: Number(start.toFixed(2)),
        end: Number(end.toFixed(2)),
        text,
        words: rebuildWordTimings(text, start, end),
      },
    ]);
  };

  const removeCue = (idx: number) => {
    onChange(cues.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-zinc-300">
          Реплики субтитров ({cues.length})
        </span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={onResync}
            disabled={resyncing}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-[11px] text-zinc-200"
            title="Пересобрать субтитры из транскрипта Whisper"
          >
            <RefreshCw className={`w-3 h-3 ${resyncing ? "animate-spin" : ""}`} />
            Из речи
          </button>
          <button
            onClick={addCue}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-accent-600 hover:bg-accent-500 text-[11px] font-medium text-white"
          >
            <Plus className="w-3 h-3" />
            Фраза
          </button>
        </div>
      </div>

      <div className="max-h-64 overflow-y-auto space-y-2 pr-1">
        {cues.length === 0 ? (
          <div className="text-xs text-zinc-500 py-4 text-center border border-dashed border-surface-800 rounded-lg">
            Нет реплик. Нажмите «Из речи» после анализа или добавьте фразу вручную.
          </div>
        ) : (
          cues.map((cue, idx) => (
            <div
              key={cue.id}
              className="p-2 rounded-lg bg-surface-950 border border-surface-800 space-y-1.5 text-xs"
            >
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={clipDuration}
                  value={cue.start}
                  onChange={(e) => updateCue(idx, { start: Number(e.target.value) })}
                  className="w-16 px-1.5 py-0.5 rounded bg-surface-900 border border-surface-700 text-zinc-200 font-mono text-[11px]"
                />
                <span className="text-zinc-500">–</span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={clipDuration}
                  value={cue.end}
                  onChange={(e) => updateCue(idx, { end: Number(e.target.value) })}
                  className="w-16 px-1.5 py-0.5 rounded bg-surface-900 border border-surface-700 text-zinc-200 font-mono text-[11px]"
                />
                <span className="flex-1" />
                <button
                  onClick={() => removeCue(idx)}
                  className="text-zinc-500 hover:text-rose-400"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
              <input
                value={cue.text}
                onChange={(e) => updateCue(idx, { text: e.target.value })}
                className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100 text-xs"
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
