import { SpacetimeViewport } from './SpacetimeViewport';

const SPEED_STEPS = [0.25, 0.5, 1.0, 1.5, 2.0, 4.0];

export interface PlaybackSnapshot {
  currentNs: number;
  totalNs: number;
  playing: boolean;
  speed: number;
  loop: boolean;
}

export class PlaybackController {
  private viewports: SpacetimeViewport[] = [];
  private totalDurationNs = 100_000_000;
  private currentNs = 0;
  private playing = true;
  private loop = true;
  private speedIdx = 2; // 1.0x default
  private baseSimSeconds = 5.5; // Target ~5.5s playback at 1.0x so 80ms-350ms traces are comfortable to watch!

  private rafId = 0;
  private lastFrameMs = 0;
  private holdEndUntilMs = 0;
  private listeners: Array<(snap: PlaybackSnapshot) => void> = [];

  public attachViewports(vps: SpacetimeViewport[]): void {
    this.viewports = vps;
    for (const vp of this.viewports) {
      vp.setCurrentTimeNs(this.currentNs);
    }
  }

  public configureDuration(totalNs: number, autoPlay = true): void {
    this.totalDurationNs = Math.max(1, totalNs);
    this.currentNs = 0;
    this.playing = autoPlay;
    this.holdEndUntilMs = 0;
    this.pushToViewports();
    this.emit();
  }

  public onChange(fn: (snap: PlaybackSnapshot) => void): () => void {
    this.listeners.push(fn);
    fn(this.snapshot());
    return () => {
      this.listeners = this.listeners.filter((x) => x !== fn);
    };
  }

  public snapshot(): PlaybackSnapshot {
    return {
      currentNs: this.currentNs,
      totalNs: this.totalDurationNs,
      playing: this.playing,
      speed: SPEED_STEPS[this.speedIdx],
      loop: this.loop,
    };
  }

  public togglePlay(): void {
    if (!this.playing && this.currentNs >= this.totalDurationNs) {
      this.currentNs = 0;
    }
    this.playing = !this.playing;
    this.holdEndUntilMs = 0;
    this.emit();
  }

  public restart(): void {
    this.currentNs = 0;
    this.playing = true;
    this.holdEndUntilMs = 0;
    this.pushToViewports();
    this.emit();
  }

  public seekFraction(frac: number): void {
    const clamped = Math.max(0, Math.min(1, frac));
    this.currentNs = Math.round(clamped * this.totalDurationNs);
    this.holdEndUntilMs = 0;
    this.pushToViewports();
    this.emit();
  }

  public seekNs(ns: number): void {
    this.currentNs = Math.max(0, Math.min(this.totalDurationNs, ns));
    this.holdEndUntilMs = 0;
    this.pushToViewports();
    this.emit();
  }

  public slower(): void {
    this.speedIdx = Math.max(0, this.speedIdx - 1);
    this.emit();
  }

  public faster(): void {
    this.speedIdx = Math.min(SPEED_STEPS.length - 1, this.speedIdx + 1);
    this.emit();
  }

  public setSpeed(speed: number): void {
    const idx = SPEED_STEPS.indexOf(speed);
    if (idx >= 0) {
      this.speedIdx = idx;
      this.emit();
    }
  }

  public setLoop(loop: boolean): void {
    this.loop = loop;
    this.emit();
  }

  public startLoop(): void {
    if (this.rafId) return;
    this.lastFrameMs = performance.now();
    const tick = (nowMs: number) => {
      const dtSec = Math.min(0.08, Math.max(0, (nowMs - this.lastFrameMs) / 1000));
      this.lastFrameMs = nowMs;

      if (this.playing) {
        if (this.holdEndUntilMs > 0) {
          if (nowMs >= this.holdEndUntilMs) {
            this.holdEndUntilMs = 0;
            this.currentNs = 0;
            this.pushToViewports();
            this.emit();
          }
        } else {
          const speed = SPEED_STEPS[this.speedIdx];
          const nsPerRealSec =
            (this.totalDurationNs / this.baseSimSeconds) * speed;
          this.currentNs += dtSec * nsPerRealSec;

          if (this.currentNs >= this.totalDurationNs) {
            this.currentNs = this.totalDurationNs;
            this.pushToViewports();
            if (this.loop) {
              // Hold completed 3D sculpture for 1.4s before looping so learners admire the full trace
              this.holdEndUntilMs = nowMs + 1400;
            } else {
              this.playing = false;
            }
            this.emit();
          } else {
            this.pushToViewports();
            this.emit();
          }
        }
      }

      const tSec = nowMs / 1000;
      for (const vp of this.viewports) {
        vp.renderFrame(tSec);
      }

      this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
  }

  public stopLoop(): void {
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
    }
  }

  private pushToViewports(): void {
    for (const vp of this.viewports) {
      vp.setCurrentTimeNs(this.currentNs);
    }
  }

  private emit(): void {
    const snap = this.snapshot();
    for (const fn of this.listeners) {
      fn(snap);
    }
  }
}
