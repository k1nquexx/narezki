import type {
  AdPreset,
  AppSettings,
  BackgroundAsset,
  Clip,
  EncoderProbe,
  GPUInfo,
  Job,
  LLMTestResult,
  LiveChannel,
  LiveClipFeedItem,
  Project,
  PublishQueueItem,
  SocialAccount,
  SocialAuthType,
  SocialPlatform,
  SubtitleCue,
  SubtitlePreset,
  SystemHealth,
} from "../types/api";

class ApiClient {
  private baseUrl = "http://127.0.0.1:8765";

  setBaseUrl(url: string): void {
    this.baseUrl = url.replace(/\/+$/, "");
  }

  getBaseUrl(): string {
    return this.baseUrl;
  }

  mediaUrl(filePath: string | null | undefined): string {
    if (!filePath) return "";
    return `${this.baseUrl}/api/media/stream?path=${encodeURIComponent(filePath)}`;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(init?.headers ?? {}),
      },
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const err = (await res.json()) as {
          detail?: string;
          errors?: Array<{ loc?: Array<string | number>; msg?: string }>;
        };
        if (err.errors && err.errors.length > 0) {
          const items = err.errors
            .map((e) => {
              const field = e.loc?.filter((p) => p !== "body").join(".") || "input";
              return `${field}: ${e.msg || "invalid"}`;
            })
            .join("; ");
          detail = err.detail ? `${err.detail}: ${items}` : items;
        } else if (err.detail) {
          detail = err.detail;
        }
      } catch {
        const txt = await res.text().catch(() => "");
        if (txt) detail = txt;
      }
      throw new Error(detail);
    }
    return (await res.json()) as T;
  }

  // System
  health = (): Promise<SystemHealth> => this.request("/api/system/health");
  gpu = (): Promise<GPUInfo> => this.request("/api/system/gpu");
  releaseVram = (): Promise<GPUInfo> =>
    this.request("/api/system/gpu/release", { method: "POST" });
  encoders = (refresh = false): Promise<EncoderProbe> =>
    this.request(`/api/system/encoders?refresh=${refresh}`);
  getSettings = (): Promise<AppSettings> => this.request("/api/system/settings");
  updateSettings = (body: AppSettings): Promise<AppSettings> =>
    this.request("/api/system/settings", { method: "PUT", body: JSON.stringify(body) });
  testLlm = (body: {
    base_url?: string;
    api_key?: string;
    model?: string;
  }): Promise<LLMTestResult> =>
    this.request("/api/system/test-llm", { method: "POST", body: JSON.stringify(body) });

  // Jobs
  listJobs = (projectId?: string): Promise<Job[]> =>
    this.request(projectId ? `/api/jobs?project_id=${encodeURIComponent(projectId)}` : "/api/jobs");
  cancelJob = (jobId: string): Promise<Job> =>
    this.request(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  clearCompletedJobs = (): Promise<{ deleted: number }> =>
    this.request("/api/jobs/completed", { method: "DELETE" });

  // Projects
  listProjects = (): Promise<Project[]> => this.request("/api/projects");
  getProject = (id: string): Promise<Project> => this.request(`/api/projects/${id}`);
  importLocal = (file_path: string, title?: string): Promise<{ project: Project; job: Job }> =>
    this.request("/api/projects/import-local", {
      method: "POST",
      body: JSON.stringify({ file_path, title }),
    });
  importUrl = (url: string, title?: string): Promise<{ project: Project; job: Job }> =>
    this.request("/api/projects/import-url", {
      method: "POST",
      body: JSON.stringify({ url, title }),
    });
  deleteProject = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/projects/${id}`, { method: "DELETE" });
  analyzeProject = (
    id: string,
    body: {
      mode?: "auto" | "llm" | "heuristic";
      max_highlights?: number;
      min_duration?: number;
      max_duration?: number;
      focus_prompt?: string;
    } = {}
  ): Promise<Job> =>
    this.request(`/api/projects/${id}/analyze`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  basicCut = (
    projectId: string,
    body: { start_time: number; end_time: number; title?: string }
  ): Promise<{ clip: Clip; job: Job }> =>
    this.request(`/api/projects/${projectId}/basic-cut`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  exportBatch = (
    projectId: string,
    clipIds?: string[],
    outputDir?: string
  ): Promise<Job> =>
    this.request(`/api/projects/${projectId}/export-batch`, {
      method: "POST",
      body: JSON.stringify({ clip_ids: clipIds, output_dir: outputDir }),
    });

  // Clips
  listClips = (projectId: string): Promise<Clip[]> =>
    this.request(`/api/clips?project_id=${encodeURIComponent(projectId)}`);
  createClip = (body: {
    project_id: string;
    title: string;
    start_time: number;
    end_time: number;
    crop_mode?: string;
    category?: string;
  }): Promise<Clip> =>
    this.request("/api/clips", { method: "POST", body: JSON.stringify(body) });
  updateClip = (id: string, body: Partial<Clip>): Promise<Clip> =>
    this.request(`/api/clips/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  deleteClip = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/clips/${id}`, { method: "DELETE" });
  resyncSubtitles = (id: string): Promise<{ cues: SubtitleCue[]; clip: Clip }> =>
    this.request(`/api/clips/${id}/resync-subtitles`, { method: "POST" });
  trackClip = (id: string): Promise<Job> =>
    this.request(`/api/clips/${id}/track`, { method: "POST" });
  renderPreview = (id: string): Promise<Job> =>
    this.request(`/api/clips/${id}/render-preview`, { method: "POST" });
  exportClip = (id: string): Promise<Job> =>
    this.request(`/api/clips/${id}/export`, { method: "POST" });

  // Presets
  listSubtitlePresets = (): Promise<SubtitlePreset[]> => this.request("/api/presets/subtitles");
  createSubtitlePreset = (
    body: Omit<SubtitlePreset, "id" | "created_at">
  ): Promise<SubtitlePreset> =>
    this.request("/api/presets/subtitles", { method: "POST", body: JSON.stringify(body) });
  updateSubtitlePreset = (
    id: string,
    body: Partial<SubtitlePreset>
  ): Promise<SubtitlePreset> =>
    this.request(`/api/presets/subtitles/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  deleteSubtitlePreset = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/presets/subtitles/${id}`, { method: "DELETE" });

  listAdPresets = (): Promise<AdPreset[]> => this.request("/api/presets/ads");
  createAdPreset = (
    body: Omit<AdPreset, "id" | "duration" | "created_at">
  ): Promise<AdPreset> =>
    this.request("/api/presets/ads", { method: "POST", body: JSON.stringify(body) });
  uploadAdPreset = (formData: FormData): Promise<AdPreset> =>
    this.request("/api/presets/ads/upload", { method: "POST", body: formData });
  updateAdPreset = (id: string, body: Partial<AdPreset>): Promise<AdPreset> =>
    this.request(`/api/presets/ads/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  deleteAdPreset = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/presets/ads/${id}`, { method: "DELETE" });

  listBackgrounds = (): Promise<BackgroundAsset[]> => this.request("/api/presets/backgrounds");
  addBackground = (file_path: string, name?: string): Promise<BackgroundAsset> =>
    this.request("/api/presets/backgrounds", {
      method: "POST",
      body: JSON.stringify({ file_path, name }),
    });
  scanBackgroundFolder = (folder_path: string): Promise<BackgroundAsset[]> =>
    this.request("/api/presets/backgrounds/scan-folder", {
      method: "POST",
      body: JSON.stringify({ folder_path }),
    });
  deleteBackground = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/presets/backgrounds/${id}`, { method: "DELETE" });

  // Live Monitor
  listLiveChannels = (): Promise<LiveChannel[]> => this.request("/api/live/channels");
  createLiveChannel = (
    body: Partial<LiveChannel> & { channel_url: string }
  ): Promise<LiveChannel> =>
    this.request("/api/live/channels", { method: "POST", body: JSON.stringify(body) });
  updateLiveChannel = (id: string, body: Partial<LiveChannel>): Promise<LiveChannel> =>
    this.request(`/api/live/channels/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  deleteLiveChannel = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/live/channels/${id}`, { method: "DELETE" });
  startLiveChannel = (id: string): Promise<LiveChannel> =>
    this.request(`/api/live/channels/${id}/start`, { method: "POST" });
  stopLiveChannel = (id: string): Promise<LiveChannel> =>
    this.request(`/api/live/channels/${id}/stop`, { method: "POST" });
  checkLiveChannel = (
    id: string
  ): Promise<{ probe: Record<string, unknown>; channel: LiveChannel }> =>
    this.request(`/api/live/channels/${id}/check`, { method: "POST" });
  clipLiveNow = (
    id: string,
    seconds = 45
  ): Promise<{ clip_id: string; project_id: string; job_id: string }> =>
    this.request(`/api/live/channels/${id}/clip-now`, {
      method: "POST",
      body: JSON.stringify({ seconds }),
    });
  listLiveClips = (limit = 50): Promise<LiveClipFeedItem[]> =>
    this.request(`/api/live/clips?limit=${limit}`);

  // Publishing & Accounts
  listSocialAccounts = (): Promise<SocialAccount[]> =>
    this.request("/api/publishing/accounts");
  createSocialAccount = (body: {
    platform: SocialPlatform;
    name: string;
    auth_type: SocialAuthType;
    credentials_json: Record<string, unknown>;
    default_privacy?: "public" | "unlisted" | "private";
    post_gap_minutes?: number;
    daily_cap?: number;
    enabled?: boolean;
  }): Promise<SocialAccount> =>
    this.request("/api/publishing/accounts", { method: "POST", body: JSON.stringify(body) });
  updateSocialAccount = (
    id: string,
    body: Partial<SocialAccount> & { credentials_json?: Record<string, unknown> }
  ): Promise<SocialAccount> =>
    this.request(`/api/publishing/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  deleteSocialAccount = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/publishing/accounts/${id}`, { method: "DELETE" });
  getOAuthUrl = (platform: SocialPlatform): Promise<{ url: string; redirect_uri: string }> =>
    this.request(`/api/publishing/oauth/${platform}/url`);
  listPublishQueue = (status?: string): Promise<PublishQueueItem[]> =>
    this.request(`/api/publishing/queue${status ? `?status=${encodeURIComponent(status)}` : ""}`);
  enqueuePublish = (body: {
    clip_id: string;
    account_ids: string[];
    publish_now?: boolean;
    title?: string;
    caption?: string;
    privacy?: "public" | "unlisted" | "private";
  }): Promise<PublishQueueItem[]> =>
    this.request("/api/publishing/enqueue", { method: "POST", body: JSON.stringify(body) });
  cancelPublishItem = (id: string): Promise<PublishQueueItem> =>
    this.request(`/api/publishing/queue/${id}/cancel`, { method: "POST" });
  retryPublishItem = (id: string): Promise<PublishQueueItem> =>
    this.request(`/api/publishing/queue/${id}/retry`, { method: "POST" });
  deletePublishItem = (id: string): Promise<{ ok: boolean }> =>
    this.request(`/api/publishing/queue/${id}`, { method: "DELETE" });
}

export const api = new ApiClient();
