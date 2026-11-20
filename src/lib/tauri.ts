import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import type { BackendInfo } from "../types/api";

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

const FALLBACK_HTTP =
  (import.meta.env.VITE_API_URL as string | undefined) || "http://127.0.0.1:8765";

export async function getBackendInfo(): Promise<BackendInfo> {
  if (!isTauriRuntime()) {
    const wsUrl = FALLBACK_HTTP.replace(/^http/, "ws") + "/ws";
    return {
      url: FALLBACK_HTTP,
      ws_url: wsUrl,
      port: 8765,
      running: true,
      error: null,
    };
  }
  return invoke<BackendInfo>("get_backend_info");
}

export async function restartBackend(): Promise<BackendInfo> {
  if (!isTauriRuntime()) {
    return getBackendInfo();
  }
  return invoke<BackendInfo>("restart_backend");
}

export async function openInExplorer(path: string): Promise<void> {
  if (!isTauriRuntime()) {
    return;
  }
  await invoke("open_in_explorer", { path });
}

export async function openExternalUrl(url: string): Promise<void> {
  if (!isTauriRuntime()) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  await invoke("open_external_url", { url });
}

export async function listenBackendRestart(
  callback: (info: BackendInfo) => void
): Promise<UnlistenFn | null> {
  if (!isTauriRuntime()) {
    return null;
  }
  return listen<BackendInfo>("backend-restarted", (event) => {
    callback(event.payload);
  });
}

export async function pickVideoFile(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    multiple: false,
    title: "Выберите видеофайл",
    filters: [
      {
        name: "Видео",
        extensions: ["mp4", "mkv", "mov", "avi", "webm", "m4v", "ts", "flv"],
      },
    ],
  });
  return typeof selected === "string" ? selected : null;
}

export async function pickAdAssetFile(adType: "video_insert" | "overlay"): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const extensions =
    adType === "video_insert"
      ? ["mp4", "mov", "mkv", "webm", "m4v"]
      : ["png", "webp", "webm", "gif"];
  const selected = await open({
    multiple: false,
    title: adType === "video_insert" ? "Выберите рекламный ролик" : "Выберите PNG/WebM оверлей",
    filters: [{ name: "Медиа", extensions }],
  });
  return typeof selected === "string" ? selected : null;
}

export async function pickWeightsFile(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    multiple: false,
    title: "Выберите файл весов YOLOv8 (.pt)",
    filters: [{ name: "PyTorch Weights", extensions: ["pt", "onnx"] }],
  });
  return typeof selected === "string" ? selected : null;
}

export async function pickDirectory(title = "Выберите папку"): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const selected = await open({
    directory: true,
    multiple: false,
    title,
  });
  return typeof selected === "string" ? selected : null;
}
