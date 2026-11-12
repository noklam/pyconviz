export type ConcurrencyModel = 'asyncio' | 'threading';
export type LayoutHint = 'radial' | 'ring' | 'pipeline' | 'tree';
export type UnitKind = 'main' | 'task' | 'thread';

export type SpanState =
  | 'running_cpu'
  | 'waiting_gil'
  | 'suspended_await'
  | 'waiting_io'
  | 'waiting_sync'
  | 'blocking_loop';

export type EventKind =
  | 'queue_message'
  | 'lock_handoff'
  | 'gil_handoff'
  | 'event_signal'
  | 'task_result';

export type LegendKey = SpanState | EventKind;

export interface RuntimeMetadata {
  title: string;
  model: ConcurrencyModel;
  python_version: string;
  python_build: string;
  gil_enabled: boolean;
  cpu_count: number;
  duration_ns: number;
  switch_interval_s: number | null;
  layout_hint: LayoutHint;
}

export interface TraceMetrics {
  total_units: number;
  worker_units: number;
  leaked_units: number;
  message_count: number;
  sync_handoff_count: number;
  duration_ms: number;
  total_cpu_ms: number;
  total_gil_wait_ms: number;
  total_io_wait_ms: number;
  total_sync_wait_ms: number;
  total_await_wait_ms: number;
  total_blocking_loop_ms: number;
  effective_parallelism: number;
  peak_parallel_cpu_units: number;
  peak_concurrent_units: number;
  gil_wait_pct: number;
  cpu_active_pct: number;
}

export interface UnitRecord {
  id: string;
  name: string;
  kind: UnitKind;
  parent_id: string | null;
  depth: number;
  start_ns: number;
  end_ns: number;
  leaked: boolean;
}

export interface StateSpan {
  unit_id: string;
  state: SpanState;
  start_ns: number;
  end_ns: number;
  detail?: string;
}

export interface CommEvent {
  id: string;
  kind: EventKind;
  from_unit: string;
  to_unit: str;
  channel: string;
  send_ns: number;
  recv_ns: number;
  value: string;
}

type str = string;

export interface SingleTraceDocument {
  schema_version: string;
  kind?: 'single_trace';
  metadata: RuntimeMetadata;
  metrics: TraceMetrics;
  units: UnitRecord[];
  spans: StateSpan[];
  events: CommEvent[];
}

export interface ComparisonPaneData {
  label: string;
  trace: SingleTraceDocument;
  badge?: string;
  accent?: string;
}

export interface ComparisonTraceDocument {
  schema_version: string;
  kind: 'comparison_trace';
  title: string;
  subtitle: string;
  default_scale_mode: 'wall_clock' | 'normalized';
  speedup_factor: number;
  panes?: ComparisonPaneData[];
  left_label?: string;
  right_label?: string;
  left?: SingleTraceDocument;
  right?: SingleTraceDocument;
}

export type AnyTraceDocument = SingleTraceDocument | ComparisonTraceDocument;

export type CameraPreset = 'perspective' | 'top' | 'side';
export type ScaleMode = 'wall_clock' | 'normalized';

export interface TakeawayCard {
  label: string;
  value: string;
  note: string;
  tone?: 'emerald' | 'amber' | 'crimson' | 'sky';
}

export interface CodeTabFile {
  filename: string;
  label: string;
  code: string;
}

export interface LessonManifestItem {
  id: string;
  number: string;
  title: string;
  track: string;
  badge: string;
  kind: 'single' | 'comparison';
  trace_file: string;
  default_camera: CameraPreset;
  layout_hint: LayoutHint;
  summary: string;
  concept_markdown: string;
  what_to_notice: string[];
  legend_keys: LegendKey[];
  takeaways: TakeawayCard[];
  code_files: CodeTabFile[];
}

export interface CurriculumManifest {
  version: string;
  lesson_count: number;
  lessons: LessonManifestItem[];
}

export const STATE_VISUALS: Record<
  SpanState,
  { label: string; color: string; hex: number; emissive: number; radius: number }
> = {
  running_cpu: {
    label: 'Running Python Code (CPU)',
    color: '#10f5a0',
    hex: 0x10f5a0,
    emissive: 0x059669,
    radius: 0.92,
  },
  waiting_gil: {
    label: 'Waiting for GIL',
    color: '#ff7b29',
    hex: 0xff7b29,
    emissive: 0x9a3412,
    radius: 0.5,
  },
  blocking_loop: {
    label: 'Blocking Asyncio Loop!',
    color: '#ff2e63',
    hex: 0xff2e63,
    emissive: 0xbe123c,
    radius: 1.28,
  },
  waiting_io: {
    label: 'Waiting on I/O or Timer',
    color: '#38bdf8',
    hex: 0x38bdf8,
    emissive: 0x0369a1,
    radius: 0.36,
  },
  waiting_sync: {
    label: 'Waiting for Queue / Lock',
    color: '#a855f7',
    hex: 0xa855f7,
    emissive: 0x581c87,
    radius: 0.34,
  },
  suspended_await: {
    label: 'Paused at await',
    color: '#64748b',
    hex: 0x64748b,
    emissive: 0x1e293b,
    radius: 0.22,
  },
};

export const EVENT_VISUALS: Record<
  EventKind,
  { label: string; color: string; hex: number }
> = {
  queue_message: {
    label: 'Queue Message',
    color: '#ff4d6d',
    hex: 0xff4d6d,
  },
  lock_handoff: {
    label: 'Lock Handoff',
    color: '#fbbf24',
    hex: 0xfbbf24,
  },
  gil_handoff: {
    label: 'GIL Lock Handoff',
    color: '#f59e0b',
    hex: 0xf59e0b,
  },
  event_signal: {
    label: 'Wakeup Signal',
    color: '#22d3ee',
    hex: 0x22d3ee,
  },
  task_result: {
    label: 'Thread / Task Result',
    color: '#34d399',
    hex: 0x34d399,
  },
};
