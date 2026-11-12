import './styles.css';
import { PlaybackController } from './scene/PlaybackController';
import { HoverInspectInfo, SpacetimeViewport } from './scene/SpacetimeViewport';
import { highlightPython, renderMiniMarkdown } from './syntax';
import {
  AnyTraceDocument,
  CameraPreset,
  ComparisonPaneData,
  ComparisonTraceDocument,
  CurriculumManifest,
  EVENT_VISUALS,
  EventKind,
  LegendKey,
  LessonManifestItem,
  ScaleMode,
  SingleTraceDocument,
  SpanState,
  STATE_VISUALS,
  TakeawayCard,
} from './types';

type InspectorTab = 'guide' | 'code' | 'live';

const IS_BLOG_BUILD = import.meta.env.VITE_BLOG_BUILD === 'true';

function resolveComparisonPanes(comp: ComparisonTraceDocument): ComparisonPaneData[] {
  if (comp.panes && comp.panes.length >= 2) {
    return comp.panes;
  }
  if (comp.left && comp.right) {
    return [
      {
        label: comp.left_label ?? comp.left.metadata.title,
        trace: comp.left,
        accent: '#ff7b29',
      },
      {
        label: comp.right_label ?? comp.right.metadata.title,
        trace: comp.right,
        badge: comp.speedup_factor >= 1.25 ? `${comp.speedup_factor}x Faster` : undefined,
        accent: '#10f5a0',
      },
    ];
  }
  return [];
}

function inferLegendKeysFromTrace(doc: AnyTraceDocument): LegendKey[] {
  const traces: SingleTraceDocument[] =
    doc.kind === 'comparison_trace'
      ? resolveComparisonPanes(doc).map((p) => p.trace)
      : [doc];

  const stateTotals = new Map<SpanState, number>();
  const eventKinds = new Set<EventKind>();
  let maxDur = 1;

  for (const t of traces) {
    maxDur = Math.max(maxDur, t.metadata.duration_ns);
    for (const sp of t.spans) {
      stateTotals.set(
        sp.state,
        (stateTotals.get(sp.state) ?? 0) + Math.max(0, sp.end_ns - sp.start_ns)
      );
    }
    for (const ev of t.events) {
      eventKinds.add(ev.kind);
    }
  }

  const keys: LegendKey[] = [];
  const orderedStates: SpanState[] = [
    'running_cpu',
    'waiting_gil',
    'blocking_loop',
    'waiting_io',
    'waiting_sync',
    'suspended_await',
  ];
  for (const st of orderedStates) {
    if ((stateTotals.get(st) ?? 0) >= maxDur * 0.02) {
      keys.push(st);
    }
  }
  for (const ek of ['queue_message', 'gil_handoff', 'lock_handoff', 'task_result'] as EventKind[]) {
    if (eventKinds.has(ek)) {
      keys.push(ek);
    }
  }
  return keys;
}

class PythonConcurrencyApp {
  private root: HTMLElement;
  private manifest: CurriculumManifest | null = null;
  private activeLesson: LessonManifestItem | null = null;
  private customTraceTitle: string | null = null;
  private activeTrace: AnyTraceDocument | null = null;

  private playback = new PlaybackController();
  private viewports: SpacetimeViewport[] = [];
  private scaleMode: ScaleMode = 'wall_clock';
  private showLabels = true;
  private inspectorTab: InspectorTab = 'guide';
  private activeCodeIdx = 0;
  private isEmbed = false;
  private liveDraftCode = '';
  private liveRunMode = 'auto';
  private liveRunning = false;
  private liveError: string | null = null;

  private lessonNavEl!: HTMLElement;
  private stageTitleEl!: HTMLElement;
  private stageSubEl!: HTMLElement;
  private viewportsHostEl!: HTMLElement;
  private legendBarEl!: HTMLElement;
  private scaleGroupEl!: HTMLElement;
  private camBtns = new Map<CameraPreset, HTMLButtonElement>();
  private scaleBtns = new Map<ScaleMode, HTMLButtonElement>();
  private labelBtnEl!: HTMLButtonElement;
  private playBtnEl!: HTMLButtonElement;
  private speedReadoutEl!: HTMLElement;
  private scrubberEl!: HTMLInputElement;
  private timeReadoutEl!: HTMLElement;
  private inspectorBodyEl!: HTMLElement;
  private tabBtns = new Map<InspectorTab, HTMLButtonElement>();
  private tooltipEl!: HTMLElement;

  constructor(root: HTMLElement) {
    this.root = root;
    const search = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    this.isEmbed = search.get('embed') === '1' || hash.get('embed') === '1';
    this.buildShellDom();
    this.bindGlobalEvents();
    this.playback.startLoop();
  }

  public async init(): Promise<void> {
    try {
      const res = await fetch('./traces/manifest.json');
      if (res.ok) {
        this.manifest = (await res.json()) as CurriculumManifest;
        this.renderLessonSidebar();
      }
    } catch (err) {
      console.error('Failed loading curriculum manifest:', err);
    }

    // Support `pyconvis serve <trace.json>` when launched via terminal CLI
    try {
      const customRes = await fetch('./api/custom-trace');
      if (customRes.ok) {
        const doc = (await customRes.json()) as AnyTraceDocument;
        if (doc && typeof doc === 'object' && 'schema_version' in doc) {
          this.loadCustomDocument(doc, 'Custom Trace');
          this.applyUrlQueryOverrides();
          return;
        }
      }
    } catch {
      // Static mode
    }

    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const requestedId = params.get('lesson');
    const initial =
      this.manifest?.lessons.find((l) => l.id === requestedId) ??
      this.manifest?.lessons[0] ??
      null;

    if (initial) {
      await this.selectLesson(initial, false);
      this.applyUrlQueryOverrides();
    }
  }

  private applyUrlQueryOverrides(): void {
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const searchParams = new URLSearchParams(window.location.search);
    const cam = (hashParams.get('cam') ?? searchParams.get('cam')) as CameraPreset | null;
    if (cam && ['perspective', 'top', 'side'].includes(cam)) {
      this.setCameraPreset(cam, true);
    }
    const scale = (hashParams.get('scale') ?? searchParams.get('scale')) as ScaleMode | null;
    if (scale && ['wall_clock', 'normalized'].includes(scale)) {
      this.setScaleMode(scale);
    }
    const tFrac = hashParams.get('t') ?? searchParams.get('t');
    if (tFrac !== null) {
      const parsed = Number(tFrac);
      if (!Number.isNaN(parsed)) {
        if (this.playback.snapshot().playing) {
          this.playback.togglePlay();
        }
        this.playback.seekFraction(parsed);
        for (const vp of this.viewports) {
          vp.renderFrame(0);
        }
      }
    }
  }

  private buildShellDom(): void {
    this.root.innerHTML = `
      <div class="app-shell ${this.isEmbed ? 'embed-mode' : ''}">
        <aside class="sidebar">
          <div class="brand-header">
            <div class="brand-kicker">Interactive Visual Guide</div>
            <h1 class="brand-title">Python Concurrency Visualized</h1>
            <p class="brand-sub">
              How <code>threading</code>, the <strong>GIL</strong>, free-threaded Python, and <code>asyncio</code> really work—inspired by
              <a href="https://divan.dev/posts/go_concurrency_visualize/" target="_blank" rel="noreferrer">divan's Go visualizations</a>.
            </p>
          </div>
          <nav class="lesson-nav" id="lesson-nav"></nav>
        </aside>

        <main class="stage-column">
          <header class="stage-topbar">
            <div class="stage-title-wrap">
              <h2 class="stage-title" id="stage-title">Loading…</h2>
              <p class="stage-subtitle" id="stage-subtitle"></p>
            </div>
            <div class="stage-controls-right">
              <div class="pill-group" id="scale-group" style="display: none;">
                <button class="pill-btn active" data-scale="wall_clock" title="Viewports advance at real wall-clock speed">Real Time Scale</button>
                <button class="pill-btn" data-scale="normalized" title="Stretch each trace to full height">Same Height</button>
              </div>
              <div class="pill-group">
                <button class="pill-btn active" data-cam="perspective">3D View [1]</button>
                <button class="pill-btn" data-cam="top">Top-Down [2]</button>
                <button class="pill-btn" data-cam="side">Side Time [3]</button>
              </div>
              <button class="ctrl-btn" id="toggle-labels-btn" title="Show or hide message values">Labels: ON</button>
            </div>
          </header>

          <div class="viewports-host" id="viewports-host"></div>

          <div class="legend-bar" id="legend-bar"></div>

          <footer class="transport-bar">
            <button class="ctrl-btn primary" id="play-btn">Pause</button>
            <button class="ctrl-btn" id="restart-btn" title="Restart [R]">Restart</button>
            <button class="ctrl-btn" id="slower-btn" title="Slower [S]">−</button>
            <span id="speed-readout" style="font-size:12px;font-weight:700;min-width:38px;text-align:center;">1x</span>
            <button class="ctrl-btn" id="faster-btn" title="Faster [F]">+</button>
            <div class="scrubber-wrap">
              <input type="range" min="0" max="1000" value="0" class="scrubber-slider" id="scrubber-slider" />
              <span class="time-readout" id="time-readout">0 / 0 ms</span>
            </div>
          </footer>
        </main>

        <section class="inspector">
          <div class="inspector-tabs">
            <button class="tab-btn active" data-tab="guide">Lesson Guide</button>
            <button class="tab-btn" data-tab="code">Python Code</button>
            ${!IS_BLOG_BUILD ? '<button class="tab-btn" data-tab="live">Run Live</button>' : ''}
          </div>
          <div class="inspector-body" id="inspector-body"></div>
        </section>
      </div>

      <div class="hover-tooltip" id="hover-tooltip" style="display:none;"></div>
    `;

    this.lessonNavEl = this.root.querySelector('#lesson-nav')!;
    this.stageTitleEl = this.root.querySelector('#stage-title')!;
    this.stageSubEl = this.root.querySelector('#stage-subtitle')!;
    this.viewportsHostEl = this.root.querySelector('#viewports-host')!;
    this.legendBarEl = this.root.querySelector('#legend-bar')!;
    this.scaleGroupEl = this.root.querySelector('#scale-group')!;
    this.labelBtnEl = this.root.querySelector('#toggle-labels-btn')!;
    this.playBtnEl = this.root.querySelector('#play-btn')!;
    this.speedReadoutEl = this.root.querySelector('#speed-readout')!;
    this.scrubberEl = this.root.querySelector('#scrubber-slider')!;
    this.timeReadoutEl = this.root.querySelector('#time-readout')!;
    this.inspectorBodyEl = this.root.querySelector('#inspector-body')!;
    this.tooltipEl = this.root.querySelector('#hover-tooltip')!;

    this.root.querySelectorAll<HTMLButtonElement>('[data-cam]').forEach((btn) => {
      const preset = btn.dataset.cam as CameraPreset;
      this.camBtns.set(preset, btn);
      btn.addEventListener('click', () => this.setCameraPreset(preset, false));
    });

    this.root.querySelectorAll<HTMLButtonElement>('[data-scale]').forEach((btn) => {
      const mode = btn.dataset.scale as ScaleMode;
      this.scaleBtns.set(mode, btn);
      btn.addEventListener('click', () => this.setScaleMode(mode));
    });

    this.root.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((btn) => {
      const tab = btn.dataset.tab as InspectorTab;
      this.tabBtns.set(tab, btn);
      btn.addEventListener('click', () => {
        this.inspectorTab = tab;
        this.tabBtns.forEach((b, k) => b.classList.toggle('active', k === tab));
        this.renderInspector();
      });
    });

    this.labelBtnEl.addEventListener('click', () => {
      this.showLabels = !this.showLabels;
      this.labelBtnEl.textContent = `Labels: ${this.showLabels ? 'ON' : 'OFF'}`;
      for (const vp of this.viewports) vp.setShowValueLabels(this.showLabels);
    });

    this.playBtnEl.addEventListener('click', () => this.playback.togglePlay());
    this.root
      .querySelector('#restart-btn')!
      .addEventListener('click', () => this.playback.restart());
    this.root
      .querySelector('#slower-btn')!
      .addEventListener('click', () => this.playback.slower());
    this.root
      .querySelector('#faster-btn')!
      .addEventListener('click', () => this.playback.faster());

    this.scrubberEl.addEventListener('input', () => {
      const frac = Number(this.scrubberEl.value) / 1000;
      if (this.playback.snapshot().playing) {
        this.playback.togglePlay();
      }
      this.playback.seekFraction(frac);
    });

    this.playback.onChange((snap) => {
      this.playBtnEl.textContent = snap.playing ? 'Pause' : 'Play';
      this.speedReadoutEl.textContent = `${snap.speed}x`;
      const frac = snap.totalNs > 0 ? snap.currentNs / snap.totalNs : 0;
      this.scrubberEl.value = String(Math.round(frac * 1000));
      this.timeReadoutEl.textContent = `${Math.round(snap.currentNs / 1e6)} / ${Math.round(snap.totalNs / 1e6)} ms`;
    });
  }

  private bindGlobalEvents(): void {
    window.addEventListener('keydown', (ev) => {
      if (
        ev.target instanceof HTMLInputElement ||
        ev.target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (ev.code === 'Space') {
        ev.preventDefault();
        this.playback.togglePlay();
      } else if (ev.key === 'r' || ev.key === 'R') {
        this.playback.restart();
      } else if (ev.key === 's' || ev.key === 'S') {
        this.playback.slower();
      } else if (ev.key === 'f' || ev.key === 'F') {
        this.playback.faster();
      } else if (ev.key === '1') {
        this.setCameraPreset('perspective', false);
      } else if (ev.key === '2') {
        this.setCameraPreset('top', false);
      } else if (ev.key === '3') {
        this.setCameraPreset('side', false);
      }
    });
  }

  private renderLessonSidebar(): void {
    if (!this.manifest) return;
    const byTrack = new Map<string, LessonManifestItem[]>();
    for (const l of this.manifest.lessons) {
      const arr = byTrack.get(l.track) ?? [];
      arr.push(l);
      byTrack.set(l.track, arr);
    }

    this.lessonNavEl.innerHTML = '';
    for (const [track, items] of byTrack.entries()) {
      const group = document.createElement('div');
      group.className = 'track-group';
      group.innerHTML = `<div class="track-heading">${track}</div>`;

      for (const item of items) {
        const btn = document.createElement('button');
        btn.className = 'lesson-item';
        btn.dataset.lessonId = item.id;
        btn.innerHTML = `
          <div class="lesson-item-top">
            <span class="lesson-num">${item.number}</span>
            <span class="lesson-badge">${item.badge}</span>
          </div>
          <div class="lesson-name">${item.title}</div>
        `;
        btn.addEventListener('click', () => void this.selectLesson(item, true));
        group.appendChild(btn);
      }
      this.lessonNavEl.appendChild(group);
    }
  }

  private updateSidebarHighlight(): void {
    this.lessonNavEl
      .querySelectorAll<HTMLButtonElement>('.lesson-item')
      .forEach((btn) => {
        btn.classList.toggle(
          'active',
          this.activeLesson !== null && btn.dataset.lessonId === this.activeLesson.id
        );
      });
  }

  private async selectLesson(
    lesson: LessonManifestItem,
    updateHash = true
  ): Promise<void> {
    const res = await fetch(`./traces/${lesson.trace_file}`);
    if (!res.ok) {
      throw new Error(`Failed loading trace ${lesson.trace_file}`);
    }
    const doc = (await res.json()) as AnyTraceDocument;
    this.activeLesson = lesson;
    this.customTraceTitle = null;
    this.activeCodeIdx = 0;
    if (!this.liveDraftCode.trim() && lesson.code_files.length > 0) {
      this.liveDraftCode = lesson.code_files[0].code;
    }
    this.scaleMode = 'wall_clock';
    this.updateScaleButtons();
    this.updateSidebarHighlight();

    if (updateHash && !this.isEmbed) {
      window.history.replaceState(null, '', `#lesson=${lesson.id}`);
    }

    this.mountTraceDocument(
      doc,
      `${lesson.number}. ${lesson.title}`,
      lesson.summary,
      lesson.default_camera
    );
    this.renderLegendBar(lesson.legend_keys, doc);
    this.renderInspector();
  }

  private loadCustomDocument(doc: AnyTraceDocument, name: string): void {
    this.activeLesson = null;
    this.customTraceTitle = name;
    this.inspectorTab = 'guide';
    this.tabBtns.forEach((b, k) => b.classList.toggle('active', k === 'guide'));
    this.updateSidebarHighlight();

    const title =
      doc.kind === 'comparison_trace' ? doc.title : doc.metadata.title || name;
    const sub =
      doc.kind === 'comparison_trace'
        ? doc.subtitle
        : `${doc.metadata.model} trace · Python ${doc.metadata.python_version}`;

    this.mountTraceDocument(doc, title, sub, 'perspective');
    this.renderLegendBar(inferLegendKeysFromTrace(doc), doc);
    this.renderInspector();
  }

  private renderLegendBar(keys: LegendKey[], doc: AnyTraceDocument): void {
    const itemsHtml: string[] = [];

    for (const key of keys) {
      if (key in STATE_VISUALS) {
        const vis = STATE_VISUALS[key as SpanState];
        itemsHtml.push(
          `<span class="legend-item"><span class="legend-swatch" style="background:${vis.color}"></span>${vis.label}</span>`
        );
      } else if (key in EVENT_VISUALS) {
        const vis = EVENT_VISUALS[key as EventKind];
        itemsHtml.push(
          `<span class="legend-item"><span class="legend-arrow" style="background:${vis.color}"></span>${vis.label}</span>`
        );
      }
    }

    // Highlight helper thread node when viewing an asyncio trace that spawned `to_thread`
    const hasHelperThread =
      doc.kind === 'comparison_trace'
        ? resolveComparisonPanes(doc).some(
            (p) =>
              p.trace.metadata.model === 'asyncio' &&
              p.trace.units.some((u) => u.kind === 'thread')
          )
        : doc.metadata.model === 'asyncio' &&
          doc.units.some((u) => u.kind === 'thread');

    if (hasHelperThread) {
      itemsHtml.push(
        `<span class="legend-item"><span class="legend-swatch" style="background:#fbbf24;border-radius:50%;"></span>Helper OS Thread (to_thread)</span>`
      );
    }

    itemsHtml.push(
      `<span class="kbd-hint">Drag: Rotate · Scroll: Zoom · Space: Play/Pause</span>`
    );

    this.legendBarEl.innerHTML = itemsHtml.join('');
  }

  private mountTraceDocument(
    doc: AnyTraceDocument,
    title: string,
    subtitle: string,
    preset: CameraPreset
  ): void {
    this.activeTrace = doc;
    this.stageTitleEl.textContent = title;
    this.stageSubEl.textContent = subtitle;

    for (const vp of this.viewports) vp.dispose();
    this.viewports = [];
    this.viewportsHostEl.innerHTML = '';
    this.viewportsHostEl.classList.remove('comparison-2', 'comparison-3');

    const handleHover = (info: HoverInspectInfo | null) => {
      if (!info) {
        this.tooltipEl.style.display = 'none';
        return;
      }
      this.tooltipEl.style.display = 'block';
      this.tooltipEl.style.borderColor = info.accentColor;
      this.tooltipEl.style.left = `${Math.min(window.innerWidth - 330, info.screenX + 14)}px`;
      this.tooltipEl.style.top = `${Math.min(window.innerHeight - 80, info.screenY + 14)}px`;
      this.tooltipEl.innerHTML = `
        <div class="hover-tooltip-title" style="color:${info.accentColor}">${info.title}</div>
        <div class="hover-tooltip-sub">${info.subtitle}</div>
      `;
    };

    if (doc.kind === 'comparison_trace') {
      const panes = resolveComparisonPanes(doc);
      this.viewportsHostEl.classList.add(
        panes.length >= 3 ? 'comparison-3' : 'comparison-2'
      );
      this.scaleGroupEl.style.display = 'inline-flex';

      const maxDurNs = Math.max(
        1,
        ...panes.map((p) => p.trace.metadata.duration_ns)
      );

      const paneEls = panes.map((p) =>
        this.createComparisonPane(
          p.label,
          p.trace,
          p.accent ?? '#38bdf8',
          p.badge ?? null
        )
      );
      for (const pe of paneEls) {
        this.viewportsHostEl.appendChild(pe.pane);
      }

      this.viewports = paneEls.map((pe, idx) => {
        const traceDoc = panes[idx].trace;
        const vp = new SpacetimeViewport(pe.canvasHost, handleHover);
        vp.setShowValueLabels(this.showLabels);
        const refDur =
          this.scaleMode === 'wall_clock' ? maxDurNs : traceDoc.metadata.duration_ns;
        vp.loadTrace(traceDoc, refDur, preset);
        return vp;
      });

      this.playback.attachViewports(this.viewports);
      this.playback.configureDuration(maxDurNs, true);
    } else {
      this.scaleGroupEl.style.display = 'none';

      const pane = document.createElement('div');
      pane.className = 'viewport-pane';
      const modeBadge =
        doc.metadata.model === 'threading'
          ? doc.metadata.gil_enabled
            ? 'Threading (GIL ON)'
            : 'Free-Threaded 3.14t (GIL OFF)'
          : 'Asyncio (1 Thread)';
      pane.innerHTML = `
        <div class="pane-overlay-badge">
          <div class="pane-chip">
            <span class="dot" style="background:#38bdf8"></span>
            <span>${modeBadge} · ${Math.round(doc.metrics.duration_ms)} ms</span>
          </div>
        </div>
        <div class="viewport-canvas"></div>
      `;
      this.viewportsHostEl.appendChild(pane);
      const canvasHost = pane.querySelector<HTMLElement>('.viewport-canvas')!;
      const vp = new SpacetimeViewport(canvasHost, handleHover);
      vp.setShowValueLabels(this.showLabels);
      vp.loadTrace(doc, doc.metadata.duration_ns, preset);

      this.viewports = [vp];
      this.playback.attachViewports(this.viewports);
      this.playback.configureDuration(doc.metadata.duration_ns, true);
    }

    this.camBtns.forEach((btn, k) => btn.classList.toggle('active', k === preset));
  }

  private createComparisonPane(
    label: string,
    doc: SingleTraceDocument,
    dotColor: string,
    ribbon: string | null
  ): { pane: HTMLElement; canvasHost: HTMLElement } {
    const pane = document.createElement('div');
    pane.className = 'viewport-pane';
    pane.innerHTML = `
      <div class="pane-overlay-badge">
        <div class="pane-chip">
          <span class="dot" style="background:${dotColor}"></span>
          <span>${label} · ${Math.round(doc.metrics.duration_ms)} ms</span>
        </div>
        ${ribbon ? `<span class="speedup-ribbon">${ribbon}</span>` : ''}
      </div>
      <div class="viewport-canvas"></div>
    `;
    const canvasHost = pane.querySelector<HTMLElement>('.viewport-canvas')!;
    return { pane, canvasHost };
  }

  private setCameraPreset(preset: CameraPreset, immediate: boolean): void {
    this.camBtns.forEach((b, k) => b.classList.toggle('active', k === preset));
    for (const vp of this.viewports) {
      vp.setCameraPreset(preset, immediate);
    }
  }

  private setScaleMode(mode: ScaleMode): void {
    this.scaleMode = mode;
    this.updateScaleButtons();
    if (this.activeTrace && this.activeTrace.kind === 'comparison_trace') {
      const panes = resolveComparisonPanes(this.activeTrace);
      const maxDur = Math.max(
        1,
        ...panes.map((p) => p.trace.metadata.duration_ns)
      );
      this.viewports.forEach((vp, idx) => {
        const traceDoc = panes[idx]?.trace;
        if (traceDoc) {
          vp.updateReferenceDurationNs(
            mode === 'wall_clock' ? maxDur : traceDoc.metadata.duration_ns
          );
        }
      });
    }
  }

  private updateScaleButtons(): void {
    this.scaleBtns.forEach((b, k) => b.classList.toggle('active', k === this.scaleMode));
  }

  private renderInspector(): void {
    if (!IS_BLOG_BUILD && this.inspectorTab === 'live') {
      this.renderLiveRunnerTab();
      return;
    }

    if (this.inspectorTab === 'code') {
      if (!this.activeLesson || this.activeLesson.code_files.length === 0) {
        this.inspectorBodyEl.innerHTML = `
          <div class="section-label">Custom Trace</div>
          <div class="concept-prose"><p>Viewing trace <code>${this.customTraceTitle ?? 'custom'}</code>.</p></div>
        `;
        return;
      }

      const files = this.activeLesson.code_files;
      const safeIdx = Math.min(this.activeCodeIdx, files.length - 1);
      const current = files[safeIdx];

      this.inspectorBodyEl.innerHTML = `
        <div class="section-label">Python Code (${current.filename})</div>
        ${
          files.length > 1
            ? `<div class="code-file-tabs">
                ${files
                  .map(
                    (f, i) =>
                      `<button class="pill-btn ${i === safeIdx ? 'active' : ''}" data-code-idx="${i}">${f.label}</button>`
                  )
                  .join('')}
              </div>`
            : ''
        }
        <div class="code-block">${highlightPython(current.code)}</div>
      `;

      this.inspectorBodyEl
        .querySelectorAll<HTMLButtonElement>('[data-code-idx]')
        .forEach((btn) => {
          btn.addEventListener('click', () => {
            this.activeCodeIdx = Number(btn.dataset.codeIdx ?? 0);
            this.renderInspector();
          });
        });
      return;
    }

    if (!this.activeLesson) {
      this.inspectorBodyEl.innerHTML = `
        <div class="section-label">Custom Trace</div>
        <div class="concept-prose"><p>Hover any 3D pillar or arrow to inspect exact timings.</p></div>
      `;
      return;
    }

    const takeawaysHtml = this.renderTakeaways(this.activeLesson.takeaways);
    this.inspectorBodyEl.innerHTML = `
      <div class="section-label">The Big Idea</div>
      <div class="concept-prose">${renderMiniMarkdown(this.activeLesson.concept_markdown)}</div>
      <div class="section-label">What to Look For</div>
      <ul class="notice-list">
        ${this.activeLesson.what_to_notice
          .map((n) => `<li>${renderMiniMarkdown(n).replace(/^<p>|<\/p>$/g, '')}</li>`)
          .join('')}
      </ul>
      ${takeawaysHtml}
    `;
  }

  private renderTakeaways(cards: TakeawayCard[]): string {
    if (!cards || cards.length === 0) return '';
    return `
      <div class="section-label">Key Numbers from This Run</div>
      <div class="takeaways-stack">
        ${cards
          .map(
            (c) => `
            <div class="takeaway-card ${c.tone ? `tone-${c.tone}` : ''}">
              <div class="takeaway-top">
                <span class="takeaway-label">${c.label}</span>
                <span class="takeaway-value">${c.value}</span>
              </div>
              <div class="takeaway-note">${c.note}</div>
            </div>
          `
          )
          .join('')}
      </div>
    `;
  }

  private renderLiveRunnerTab(): void {
    if (!this.liveDraftCode.trim() && this.activeLesson?.code_files[0]) {
      this.liveDraftCode = this.activeLesson.code_files[0].code;
    }

    this.inspectorBodyEl.innerHTML = `
      <div class="section-label">Run Python Code Live (Local Server)</div>
      <div class="live-runner-stack">
        <textarea class="live-editor" id="live-code-editor" spellcheck="false"></textarea>
        <div class="live-controls-row">
          <select class="live-select" id="live-mode-select">
            <option value="auto" ${this.liveRunMode === 'auto' ? 'selected' : ''}>Auto (asyncio or threading)</option>
            <option value="compare_gil" ${this.liveRunMode === 'compare_gil' ? 'selected' : ''}>Compare GIL=1 vs GIL=0 (3.14t)</option>
            <option value="threading_gil1" ${this.liveRunMode === 'threading_gil1' ? 'selected' : ''}>Threading (PYTHON_GIL=1)</option>
            <option value="threading_gil0" ${this.liveRunMode === 'threading_gil0' ? 'selected' : ''}>Free-Threaded (PYTHON_GIL=0)</option>
          </select>
          <button class="ctrl-btn primary" id="run-live-btn" ${this.liveRunning ? 'disabled' : ''}>
            ${this.liveRunning ? 'Running…' : 'Run in 3D'}
          </button>
        </div>
        <div class="live-controls-row">
          <button class="ctrl-btn" id="copy-lesson-code-btn" style="flex:1;">Load Active Lesson Code</button>
          <label class="ctrl-btn" style="flex:1;cursor:pointer;">
            <input type="file" accept=".json,application/json" id="live-json-upload" hidden />
            <span>Open Trace .json</span>
          </label>
        </div>
        ${this.liveError ? `<div class="live-error">${this.liveError.replace(/</g, '&lt;')}</div>` : ''}
        <div class="live-cli-box">
          <strong>Terminal CLI</strong>
          <pre>uv run pyconvis trace script.py --serve
uv run pyconvis compare-gil cpu_pool.py --serve</pre>
        </div>
      </div>
    `;

    const editor = this.inspectorBodyEl.querySelector<HTMLTextAreaElement>(
      '#live-code-editor'
    )!;
    editor.value = this.liveDraftCode;
    editor.addEventListener('input', () => {
      this.liveDraftCode = editor.value;
    });

    const modeSelect = this.inspectorBodyEl.querySelector<HTMLSelectElement>(
      '#live-mode-select'
    )!;
    modeSelect.addEventListener('change', () => {
      this.liveRunMode = modeSelect.value;
    });

    this.inspectorBodyEl
      .querySelector('#copy-lesson-code-btn')
      ?.addEventListener('click', () => {
        if (this.activeLesson?.code_files[this.activeCodeIdx]) {
          this.liveDraftCode = this.activeLesson.code_files[this.activeCodeIdx].code;
          this.renderLiveRunnerTab();
        }
      });

    const jsonInput = this.inspectorBodyEl.querySelector<HTMLInputElement>(
      '#live-json-upload'
    )!;
    jsonInput.addEventListener('change', async () => {
      const f = jsonInput.files?.[0];
      if (!f) return;
      try {
        const parsed = JSON.parse(await f.text()) as AnyTraceDocument;
        this.liveError = null;
        this.loadCustomDocument(parsed, f.name.replace(/\.json$/i, ''));
      } catch (err) {
        this.liveError = (err as Error).message;
        this.renderLiveRunnerTab();
      }
    });

    this.inspectorBodyEl
      .querySelector('#run-live-btn')
      ?.addEventListener('click', () => void this.executeLiveCode());
  }

  private async executeLiveCode(): Promise<void> {
    this.liveRunning = true;
    this.liveError = null;
    this.renderLiveRunnerTab();
    try {
      const res = await fetch('./api/run-live', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code: this.liveDraftCode,
          run_mode: this.liveRunMode,
        }),
      });
      const payload = await res.json();
      if (!res.ok) {
        throw new Error(payload?.error || `Server error (${res.status})`);
      }
      const doc = payload as AnyTraceDocument;
      this.activeLesson = null;
      this.customTraceTitle = 'Live Python Run';
      this.updateSidebarHighlight();
      const title =
        doc.kind === 'comparison_trace' ? doc.title : 'Live Python Run';
      const sub =
        doc.kind === 'comparison_trace'
          ? doc.subtitle
          : `${doc.metadata.model} · ${Math.round(doc.metrics.duration_ms)} ms`;
      this.mountTraceDocument(
        doc,
        title,
        sub,
        doc.kind === 'comparison_trace' ? 'side' : 'perspective'
      );
      this.renderLegendBar(inferLegendKeysFromTrace(doc), doc);
    } catch (err) {
      this.liveError = (err as Error).message;
    } finally {
      this.liveRunning = false;
      if (this.inspectorTab === 'live') {
        this.renderLiveRunnerTab();
      }
    }
  }
}

const appRoot = document.querySelector<HTMLElement>('#app')!;
const app = new PythonConcurrencyApp(appRoot);
void app.init();
