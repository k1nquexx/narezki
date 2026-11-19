import { useEffect, useRef } from "react";
import { Terminal, Trash2, X } from "lucide-react";
import { useAppStore } from "../store/useAppStore";

export function LogDrawer() {
  const open = useAppStore((s) => s.logDrawerOpen);
  const setOpen = useAppStore((s) => s.setLogDrawerOpen);
  const logs = useAppStore((s) => s.logs);
  const clearLogs = useAppStore((s) => s.clearLogs);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (open) {
      endRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs.length, open]);

  if (!open) return null;

  return (
    <div className="h-56 border-t border-surface-700 bg-surface-950 flex flex-col shrink-0">
      <div className="flex items-center justify-between px-4 py-1.5 border-b border-surface-800 bg-surface-900">
        <div className="flex items-center gap-2 text-xs font-medium text-zinc-300">
          <Terminal className="w-3.5 h-3.5 text-accent-400" />
          <span>Живой журнал событий ({logs.length})</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={clearLogs}
            className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200 px-2 py-0.5 rounded hover:bg-surface-800"
          >
            <Trash2 className="w-3 h-3" />
            Очистить
          </button>
          <button
            onClick={() => setOpen(false)}
            className="text-zinc-400 hover:text-zinc-200 p-0.5 rounded hover:bg-surface-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-2 font-mono text-xs space-y-1">
        {logs.length === 0 ? (
          <div className="text-zinc-500">Логи появятся здесь во время импорта, анализа и рендера.</div>
        ) : (
          logs.map((entry) => {
            const color =
              entry.level === "error"
                ? "text-rose-400"
                : entry.level === "warning"
                ? "text-amber-300"
                : "text-zinc-300";
            const time = entry.timestamp.slice(11, 19);
            return (
              <div key={entry.id} className="flex items-start gap-2 leading-relaxed">
                <span className="text-zinc-500 shrink-0">{time}</span>
                {entry.stage && (
                  <span className="text-accent-400 shrink-0">[{entry.stage}]</span>
                )}
                <span className={`${color} break-all`}>{entry.message}</span>
              </div>
            );
          })
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
