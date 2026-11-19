import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { useAppStore } from "../store/useAppStore";

export function ToastContainer() {
  const toasts = useAppStore((s) => s.toasts);
  const removeToast = useAppStore((s) => s.removeToast);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-12 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      {toasts.map((t) => {
        const icon =
          t.type === "success" ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          ) : t.type === "error" ? (
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          ) : t.type === "warning" ? (
            <TriangleAlert className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          ) : (
            <Info className="w-4 h-4 text-accent-400 shrink-0 mt-0.5" />
          );

        return (
          <div
            key={t.id}
            className="pointer-events-auto flex items-start gap-2.5 rounded-lg border border-surface-700 bg-surface-900/95 backdrop-blur px-3.5 py-2.5 shadow-xl text-sm"
          >
            {icon}
            <div className="flex-1 min-w-0">
              <div className="font-medium text-zinc-100">{t.title}</div>
              {t.message && (
                <div className="text-xs text-zinc-400 mt-0.5 break-words">{t.message}</div>
              )}
            </div>
            <button
              onClick={() => removeToast(t.id)}
              className="text-zinc-500 hover:text-zinc-200 transition-colors"
              aria-label="Закрыть уведомление"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
