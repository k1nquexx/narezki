export type TabId = "projects" | "studio" | "presets" | "settings";

export type CropMode = "auto_track" | "cinema_blur" | "split_screen";
export type ClipCategory = "dialogue" | "action" | "hybrid" | "manual";
export type KaraokeMode = "karaoke_fill" | "word_highlight" | "phrase";
export type AdType = "video_insert" | "overlay";
export type AdInsertPosition = "mid_roll" | "outro";

export interface WordTiming {
  word: string;
  start: number;
  end: number;
}

export interface SubtitleCue {
  id: string;
  start: number;
  end: number;
  text: string;
  words: WordTiming[];
}

export interface TrackPoint {
  t: number;
  cx: number;
  cy: number;
  conf: number;
}

export interface TrackData {
  source_width: number;
  source_height: number;
  clip_start: number;
  clip_end: number;
  points: TrackPoint[];
}

export interface AntiCidConfig {
  zoom: number;
  speed: number;
  brightness: number;
  contrast: number;
  saturation: number;
  strip_metadata: boolean;
}

export interface AdOverrideConfig {
  insert_position?: AdInsertPosition | null;
  insert_at_sec?: number | null;
  overlay_x?: number | null;
  overlay_y?: number | null;
  overlay_scale?: number | null;
  overlay_start?: number | null;
  overlay_end?: number | null;
  fade_duration?: number | null;
}

export interface AudioPeakEvent {
  start: number;
  end: number;
  peak_db: number;
  delta_db: number;
  score: number;
}

export interface ActionBurstEvent {
  start: number;
  end: number;
  cut_count: number;
}

export interface ProjectAnalysis {
  scenes?: number[];
  action_bursts?: ActionBurstEvent[];
  audio_peaks?: {
    sample_step: number;
    rms_db: number[];
    peaks: AudioPeakEvent[];
    median_db?: number;
    duration: number;
  };
  transcript?: Array<{
    start: number;
    end: number;
    text: string;
    words?: WordTiming[];
  }>;
  words?: WordTiming[];
  language?: string;
  summary?: string;
}

export interface Project {
  id: string;
  title: string;
  source_type: string;
  source_url: string | null;
  source_path: string;
  proxy_path: string | null;
  thumbnail_path: string | null;
  duration: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string | null;
  audio_codec: string | null;
  has_audio: boolean;
  status: string;
  error_message: string | null;
  analysis_json: ProjectAnalysis | null;
  clip_count: number;
  created_at: string;
  updated_at: string;
}

export interface Clip {
  id: string;
  project_id: string;
  title: string;
  hook_text: string;
  hook_enabled: boolean;
  description: string;
  tags_json: string[];
  virality_score: number;
  category: ClipCategory;
  reason: string;
  start_time: number;
  end_time: number;
  crop_mode: CropMode;
  background_asset_id: string | null;
  subtitle_preset_id: string | null;
  subtitles_enabled: boolean;
  subtitles_json: SubtitleCue[];
  track_data_json: TrackData | null;
  ad_preset_id: string | null;
  ad_override_json: AdOverrideConfig | null;
  anti_cid_enabled: boolean;
  anti_cid_json: AntiCidConfig | null;
  selected_for_export: boolean;
  preview_path: string | null;
  export_path: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface SubtitlePreset {
  id: string;
  name: string;
  font_name: string;
  font_size: number;
  primary_color: string;
  highlight_color: string;
  outline_color: string;
  outline_width: number;
  shadow_depth: number;
  bold: boolean;
  uppercase: boolean;
  margin_bottom: number;
  words_per_line: number;
  karaoke_mode: KaraokeMode;
  hook_bg_color: string;
  hook_text_color: string;
  is_default: boolean;
  created_at: string;
}

export interface AdPreset {
  id: string;
  name: string;
  ad_type: AdType;
  asset_path: string;
  insert_position: AdInsertPosition;
  insert_at_sec: number | null;
  overlay_x: number;
  overlay_y: number;
  overlay_scale: number;
  overlay_start: number;
  overlay_end: number;
  fade_duration: number;
  duration: number;
  created_at: string;
}

export interface BackgroundAsset {
  id: string;
  name: string;
  file_path: string;
  duration: number;
  width: number;
  height: number;
  thumbnail_path: string | null;
  created_at: string;
}

export interface Job {
  id: string;
  job_type: string;
  project_id: string | null;
  clip_id: string | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  stage: string;
  progress: number;
  message: string;
  payload_json: Record<string, unknown>;
  result_json: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface GPUInfo {
  cuda_available: boolean;
  device_name: string;
  vram_total_mb: number;
  vram_used_mb: number;
  vram_free_mb: number;
  torch_allocated_mb: number;
  torch_reserved_mb: number;
  driver_version: string | null;
}

export interface EncoderProbe {
  selected: string;
  available: string[];
  details: Record<string, boolean>;
}

export interface AppSettings {
  llm_base_url: string;
  llm_api_key: string;
  llm_model: string;
  llm_temperature: number;
  llm_max_highlights: number;
  llm_chunk_minutes: number;

  whisper_model_size: "large-v3-turbo" | "medium" | "small" | "base" | "large-v3";
  whisper_compute_type: "float16" | "int8_float16" | "int8";
  whisper_device: "cuda" | "cpu";
  whisper_language: string;

  preferred_encoder: "auto" | "h264_nvenc" | "hevc_nvenc" | "h264_amf" | "h264_qsv" | "libx264";
  target_fps: number;
  crf_or_cq: number;
  max_render_workers: number;
  export_dir: string;

  yolo_model_path: string;
  tracker_sample_fps: number;
  smoothing_method: "kalman" | "ema";
  smoothing_strength: number;

  default_anti_cid_enabled: boolean;
  default_anti_cid_zoom: number;
  default_anti_cid_speed: number;
  default_anti_cid_brightness: number;
  default_anti_cid_contrast: number;
  default_anti_cid_saturation: number;
  default_anti_cid_strip_metadata: boolean;
}

export interface LLMTestResult {
  ok: boolean;
  model: string;
  latency_ms: number;
  reply: string;
  error: string | null;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  level: "debug" | "info" | "warning" | "error";
  message: string;
  job_id?: string | null;
  stage?: string | null;
}

export interface BackendInfo {
  url: string;
  ws_url: string;
  port: number;
  running: boolean;
  error: string | null;
}

export interface SystemHealth {
  status: string;
  ffmpeg_ok: boolean;
  ffprobe_ok: boolean;
  ffmpeg_path: string | null;
  ffprobe_path: string | null;
  cuda_available: boolean;
  gpu_name: string;
}
