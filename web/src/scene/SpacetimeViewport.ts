import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
  CameraPreset,
  CommEvent,
  EVENT_VISUALS,
  SingleTraceDocument,
  STATE_VISUALS,
  UnitRecord,
} from '../types';
import { computeUnitLayout, UnitAnchor } from './layout';

export interface HoverInspectInfo {
  title: string;
  subtitle: string;
  accentColor: string;
  screenX: number;
  screenY: number;
}

interface DynamicSpanMesh {
  mesh: THREE.Mesh;
  state: string;
  startNs: number;
  endNs: number;
  x: number;
  z: number;
}

interface DynamicUnitVisual {
  unit: UnitRecord;
  anchor: UnitAnchor;
  headerGroup: THREE.Group;
  spawnBridge: THREE.Line | null;
  joinBridge: THREE.Line | null;
  leakHalo: THREE.Mesh | null;
  backboneLine: THREE.Line;
  backbonePositions: Float32Array;
}

interface DynamicArrowVisual {
  event: CommEvent;
  flightStartNs: number;
  flightEndNs: number;
  startPos: THREE.Vector3;
  endPos: THREE.Vector3;
  dirNorm: THREE.Vector3;
  totalLen: number;
  shaftMesh: THREE.Mesh;
  headMesh: THREE.Mesh;
  pulseSphere: THREE.Mesh;
  labelSprite: THREE.Sprite | null;
}

const SCENE_HEIGHT = 96; // Vertical 3D span for referenceDurationNs
const TOP_Y = SCENE_HEIGHT / 2;

function createTextSprite(
  text: string,
  options: {
    bgColor: string;
    borderColor: string;
    textColor: string;
    fontSize?: number;
    worldScale?: number;
  }
): THREE.Sprite {
  const fontSize = options.fontSize ?? 28;
  const padX = 16;
  const padY = 8;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  ctx.font = `600 ${fontSize}px "Inter", "SF Pro Display", -apple-system, sans-serif`;
  const textWidth = Math.ceil(ctx.measureText(text).width);

  canvas.width = Math.max(64, textWidth + padX * 2);
  canvas.height = fontSize + padY * 2 + 6;

  ctx.font = `600 ${fontSize}px "Inter", "SF Pro Display", -apple-system, sans-serif`;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';

  const r = Math.min(14, canvas.height / 2);
  ctx.fillStyle = options.bgColor;
  ctx.strokeStyle = options.borderColor;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.roundRect(2, 2, canvas.width - 4, canvas.height - 4, r);
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = options.textColor;
  ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 1);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;

  const mat = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: true,
    depthWrite: false,
  });
  const sprite = new THREE.Sprite(mat);
  const scaleFactor = options.worldScale ?? 0.065;
  sprite.scale.set(canvas.width * scaleFactor, canvas.height * scaleFactor, 1);
  return sprite;
}

export class SpacetimeViewport {
  private container: HTMLElement;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private traceGroup: THREE.Group;
  private scanPlaneMesh: THREE.Mesh;
  private gilCollarGroup: THREE.Group | null = null;
  private gilCollarRing: THREE.Mesh | null = null;

  private trace: SingleTraceDocument | null = null;
  private referenceDurationNs = 1;
  private currentTimeNs = 0;
  private showValueLabels = true;
  private maxLayoutRadius = 32;
  private currentPreset: CameraPreset = 'perspective';
  private userManipulatedCamera = false;

  private dynamicSpans: DynamicSpanMesh[] = [];
  private dynamicUnits: DynamicUnitVisual[] = [];
  private dynamicArrows: DynamicArrowVisual[] = [];
  private pickables: THREE.Object3D[] = [];

  private targetCamPos: THREE.Vector3 | null = null;
  private targetLookAt: THREE.Vector3 | null = null;

  private raycaster = new THREE.Raycaster();
  private pointerNdc = new THREE.Vector2();
  private onHoverCallback?: (info: HoverInspectInfo | null) => void;
  private resizeObserver: ResizeObserver;
  private disposed = false;

  private unitCylGeo = new THREE.CylinderGeometry(1, 1, 1, 14, 1, false);
  private unitConeGeo = new THREE.ConeGeometry(1, 1, 12);
  private unitSphereGeo = new THREE.SphereGeometry(1, 14, 14);
  private unitTorusGeo = new THREE.TorusGeometry(2.1, 0.28, 10, 28);
  private gilTorusGeo = new THREE.TorusGeometry(1.75, 0.18, 10, 32);

  constructor(
    container: HTMLElement,
    onHover?: (info: HoverInspectInfo | null) => void
  ) {
    this.container = container;
    this.onHoverCallback = onHover;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x070b14);
    this.scene.fog = new THREE.FogExp2(0x070b14, 0.0008);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.5, 2200);

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    container.appendChild(this.renderer.domElement);

    this.syncRendererSize();

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxDistance = 900;
    this.controls.minDistance = 16;
    this.controls.target.set(0, 0, 0);

    this.controls.addEventListener('start', () => {
      this.targetCamPos = null;
      this.targetLookAt = null;
    });

    this.renderer.domElement.addEventListener('pointerdown', this.markUserCameraInput);
    this.renderer.domElement.addEventListener('wheel', this.markUserCameraInput, {
      passive: true,
    });

    const ambient = new THREE.AmbientLight(0xdbeafe, 0.88);
    this.scene.add(ambient);

    const keyLight = new THREE.DirectionalLight(0xffffff, 1.35);
    keyLight.position.set(65, 110, 85);
    this.scene.add(keyLight);

    const rimLight = new THREE.DirectionalLight(0x38bdf8, 0.65);
    rimLight.position.set(-80, -30, -65);
    this.scene.add(rimLight);

    this.traceGroup = new THREE.Group();
    this.scene.add(this.traceGroup);

    const scanGeo = new THREE.CircleGeometry(1, 48);
    scanGeo.rotateX(-Math.PI / 2);
    const scanMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0.045,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    this.scanPlaneMesh = new THREE.Mesh(scanGeo, scanMat);
    this.scanPlaneMesh.scale.set(40, 1, 40);
    this.scene.add(this.scanPlaneMesh);

    this.renderer.domElement.addEventListener('pointermove', this.handlePointerMove);
    this.renderer.domElement.addEventListener('pointerleave', this.handlePointerLeave);

    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.container);
  }

  private markUserCameraInput = (): void => {
    this.userManipulatedCamera = true;
  };

  private syncRendererSize(): { w: number; h: number } {
    const rect = this.container.getBoundingClientRect();
    const rawW = rect.width || this.container.clientWidth || 640;
    const rawH = rect.height || this.container.clientHeight || 540;
    const w = Math.max(120, Math.round(rawW));
    const h = Math.max(120, Math.round(rawH));
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    return { w, h };
  }

  private nsToY(ns: number): number {
    const ratio = Math.max(0, Math.min(1.5, ns / Math.max(1, this.referenceDurationNs)));
    return TOP_Y - ratio * SCENE_HEIGHT;
  }

  public loadTrace(
    doc: SingleTraceDocument,
    referenceDurationNs?: number,
    preset?: CameraPreset
  ): void {
    this.clearTraceGroup();
    this.trace = doc;
    this.referenceDurationNs = Math.max(
      1,
      referenceDurationNs ?? doc.metadata.duration_ns
    );

    const anchors = computeUnitLayout(doc.units, doc.metadata.layout_hint || 'radial');
    let maxR = 22;
    for (const a of anchors.values()) {
      const dist = Math.hypot(a.x, a.z);
      if (dist > maxR) maxR = dist;
    }
    this.maxLayoutRadius = maxR + 8;
    this.scanPlaneMesh.scale.set(this.maxLayoutRadius, 1, this.maxLayoutRadius);

    this.buildStageReferenceGrid(doc, this.maxLayoutRadius);
    this.buildUnitsAndConnectors(doc, anchors);
    this.buildStateSpans(doc, anchors);
    this.buildCommArrows(doc, anchors);
    this.buildGilCollar(doc);

    if (preset) {
      this.currentPreset = preset;
    }
    this.userManipulatedCamera = false;
    this.setCameraPreset(this.currentPreset, true);
    this.setCurrentTimeNs(this.currentTimeNs);

    // Re-sync on next animation frame once CSS grid layout is 100% finalized
    requestAnimationFrame(() => {
      if (!this.disposed && !this.userManipulatedCamera) {
        this.handleResize();
      }
    });
  }

  public updateReferenceDurationNs(refNs: number): void {
    if (!this.trace) return;
    this.loadTrace(this.trace, refNs, this.currentPreset);
  }

  public setShowValueLabels(show: boolean): void {
    this.showValueLabels = show;
    this.setCurrentTimeNs(this.currentTimeNs);
  }

  /**
   * Computes exact frustum-fitting camera position & target for any viewport aspect ratio
   * (1-column, 2-column, or narrow 3-column comparison) so the whole diagram fits cleanly.
   */
  public setCameraPreset(preset: CameraPreset, immediate = false): void {
    this.currentPreset = preset;
    this.userManipulatedCamera = false;
    this.syncRendererSize();

    const r = Math.max(30, this.maxLayoutRadius);
    const vFovRad = THREE.MathUtils.degToRad(this.camera.fov);
    const tanV = Math.tan(vFovRad * 0.5);
    const tanH = tanV * Math.max(0.22, this.camera.aspect);

    // Full vertical reference bounds (+extra top margin so floating pane chip never overlaps 0ms labels)
    const yTopBound = TOP_Y + 15;
    const yBotBound = -TOP_Y - 7;
    const midY = (yTopBound + yBotBound) * 0.5;
    const spanH = yTopBound - yBotBound;
    // Full horizontal half-width including left-side millisecond ruler (-r - 11) and right labels (+r + 8)
    const halfW = r + 11.5;

    let pos: THREE.Vector3;
    let look: THREE.Vector3;

    if (preset === 'top') {
      const planHalf = r + 10.5;
      const dist = Math.max((planHalf * 1.18) / tanV, (planHalf * 1.06) / tanH);
      look = new THREE.Vector3(-1.5, TOP_Y, -1.5);
      pos = new THREE.Vector3(-1.5, TOP_Y + dist, -1.49);
    } else if (preset === 'side') {
      const distV = (spanH * 0.5) / tanV;
      const distH = halfW / tanH;
      const dist = Math.max(distV, distH) * 1.05;
      look = new THREE.Vector3(-2, midY, 0);
      pos = new THREE.Vector3(-2, midY + 1.5, dist);
    } else {
      const el = THREE.MathUtils.degToRad(21);
      const az = THREE.MathUtils.degToRad(34);
      const projHalfH = spanH * 0.5 * Math.cos(el) + r * Math.sin(el);
      const projHalfW = halfW * 1.04;
      const dist = Math.max(projHalfH / tanV, projHalfW / tanH) * 1.06;
      look = new THREE.Vector3(-1.5, midY - 1.5, 0);
      pos = new THREE.Vector3(
        look.x + Math.sin(az) * Math.cos(el) * dist,
        look.y + Math.sin(el) * dist,
        look.z + Math.cos(az) * Math.cos(el) * dist
      );
    }

    const camDist = pos.distanceTo(look);
    this.controls.maxDistance = Math.max(700, camDist * 2.5);
    this.camera.far = Math.max(2000, camDist * 4);
    this.camera.updateProjectionMatrix();

    if (immediate) {
      this.camera.position.copy(pos);
      this.controls.target.copy(look);
      this.controls.update();
      this.targetCamPos = null;
      this.targetLookAt = null;
    } else {
      this.targetCamPos = pos;
      this.targetLookAt = look;
    }
  }

  private buildStageReferenceGrid(doc: SingleTraceDocument, stageRadius: number): void {
    for (const frac of [0.5, 1.0]) {
      const ringPts: THREE.Vector3[] = [];
      const segs = 64;
      const rad = stageRadius * frac;
      for (let i = 0; i <= segs; i++) {
        const th = (2 * Math.PI * i) / segs;
        ringPts.push(new THREE.Vector3(Math.cos(th) * rad, TOP_Y, Math.sin(th) * rad));
      }
      const geo = new THREE.BufferGeometry().setFromPoints(ringPts);
      const mat = new THREE.LineBasicMaterial({
        color: 0x1e293b,
        transparent: true,
        opacity: frac === 1.0 ? 0.65 : 0.35,
      });
      this.traceGroup.add(new THREE.Line(geo, mat));
    }

    const endY = this.nsToY(doc.metadata.duration_ns);
    const endPts: THREE.Vector3[] = [];
    for (let i = 0; i <= 64; i++) {
      const th = (2 * Math.PI * i) / 64;
      endPts.push(
        new THREE.Vector3(
          Math.cos(th) * (stageRadius * 0.92),
          endY,
          Math.sin(th) * (stageRadius * 0.92)
        )
      );
    }
    this.traceGroup.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(endPts),
        new THREE.LineBasicMaterial({
          color: 0x334155,
          transparent: true,
          opacity: 0.55,
        })
      )
    );

    const refMs = this.referenceDurationNs / 1_000_000;
    const tickStepMs =
      refMs <= 60 ? 10 : refMs <= 180 ? 25 : refMs <= 450 ? 50 : 100;
    const rulerX = -stageRadius - 4;
    const axisGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(rulerX, TOP_Y, 0),
      new THREE.Vector3(rulerX, -TOP_Y, 0),
    ]);
    this.traceGroup.add(
      new THREE.Line(
        axisGeo,
        new THREE.LineBasicMaterial({ color: 0x1e293b, transparent: true, opacity: 0.7 })
      )
    );

    for (let ms = 0; ms <= refMs + 0.1; ms += tickStepMs) {
      const y = this.nsToY(ms * 1_000_000);
      const tickSprite = createTextSprite(`${Math.round(ms)}ms`, {
        bgColor: 'rgba(15, 23, 42, 0.78)',
        borderColor: 'rgba(51, 65, 85, 0.7)',
        textColor: '#94a3b8',
        fontSize: 22,
        worldScale: 0.048,
      });
      tickSprite.position.set(rulerX - 3.5, y, 0);
      this.traceGroup.add(tickSprite);
    }
  }

  private buildUnitsAndConnectors(
    doc: SingleTraceDocument,
    anchors: Map<string, UnitAnchor>
  ): void {
    const showAllNames = doc.units.length <= 20;

    for (const u of doc.units) {
      const anchor = anchors.get(u.id)!;
      const startY = this.nsToY(u.start_ns);
      const endY = this.nsToY(u.end_ns);
      const isHelperThread = doc.metadata.model === 'asyncio' && u.kind === 'thread';

      const headerGroup = new THREE.Group();
      headerGroup.position.set(anchor.x, startY, anchor.z);

      const nodeMat = new THREE.MeshStandardMaterial({
        color: isHelperThread ? 0xfbbf24 : u.id === 'main' ? 0x38bdf8 : 0x60a5fa,
        emissive: isHelperThread ? 0xb45309 : u.id === 'main' ? 0x0284c7 : 0x1d4ed8,
        emissiveIntensity: 0.7,
        roughness: 0.25,
      });
      const nodeMesh = new THREE.Mesh(this.unitSphereGeo, nodeMat);
      const nodeRad = u.id === 'main' ? 1.15 : isHelperThread ? 0.95 : 0.78;
      nodeMesh.scale.setScalar(nodeRad);
      headerGroup.add(nodeMesh);

      if (u.id === 'main' || isHelperThread || showAllNames) {
        const badge = createTextSprite(u.name, {
          bgColor: isHelperThread
            ? 'rgba(120, 53, 15, 0.92)'
            : u.id === 'main'
              ? 'rgba(14, 116, 144, 0.92)'
              : 'rgba(15, 23, 42, 0.88)',
          borderColor: isHelperThread
            ? '#fbbf24'
            : u.id === 'main'
              ? '#38bdf8'
              : 'rgba(96, 165, 250, 0.65)',
          textColor: isHelperThread ? '#fef3c7' : '#f8fafc',
          fontSize: u.id === 'main' ? 26 : 22,
          worldScale: u.id === 'main' ? 0.056 : 0.046,
        });
        badge.position.set(0, 2.5, 0);
        headerGroup.add(badge);
      }
      this.traceGroup.add(headerGroup);

      let spawnBridge: THREE.Line | null = null;
      let joinBridge: THREE.Line | null = null;
      if (u.parent_id && anchors.has(u.parent_id)) {
        const pAnchor = anchors.get(u.parent_id)!;
        const bridgeColor = isHelperThread ? 0xfbbf24 : 0x3b82f6;
        const sGeo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(pAnchor.x, startY, pAnchor.z),
          new THREE.Vector3(anchor.x, startY, anchor.z),
        ]);
        spawnBridge = new THREE.Line(
          sGeo,
          new THREE.LineBasicMaterial({
            color: bridgeColor,
            transparent: true,
            opacity: isHelperThread ? 0.85 : 0.62,
          })
        );
        this.traceGroup.add(spawnBridge);

        if (!u.leaked) {
          const jGeo = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(anchor.x, endY, anchor.z),
            new THREE.Vector3(pAnchor.x, endY, pAnchor.z),
          ]);
          joinBridge = new THREE.Line(
            jGeo,
            new THREE.LineBasicMaterial({
              color: bridgeColor,
              transparent: true,
              opacity: isHelperThread ? 0.7 : 0.45,
            })
          );
          this.traceGroup.add(joinBridge);
        }
      }

      let leakHalo: THREE.Mesh | null = null;
      if (u.leaked) {
        const leakMat = new THREE.MeshStandardMaterial({
          color: 0xff2e63,
          emissive: 0xff2e63,
          emissiveIntensity: 0.9,
          roughness: 0.2,
        });
        leakHalo = new THREE.Mesh(this.unitTorusGeo, leakMat);
        leakHalo.rotation.x = Math.PI / 2;
        leakHalo.position.set(anchor.x, endY, anchor.z);
        leakHalo.userData = {
          inspectTitle: `${u.name} — LEAKED!`,
          inspectSubtitle: `Spawned at ${(u.start_ns / 1e6).toFixed(1)}ms and never stopped before exit`,
          accentColor: '#ff2e63',
        };
        this.traceGroup.add(leakHalo);
        this.pickables.push(leakHalo);
      }

      const backbonePositions = new Float32Array([
        anchor.x,
        startY,
        anchor.z,
        anchor.x,
        startY,
        anchor.z,
      ]);
      const bbGeo = new THREE.BufferGeometry();
      bbGeo.setAttribute('position', new THREE.BufferAttribute(backbonePositions, 3));
      const backboneLine = new THREE.Line(
        bbGeo,
        new THREE.LineBasicMaterial({
          color: 0x334155,
          transparent: true,
          opacity: 0.55,
        })
      );
      this.traceGroup.add(backboneLine);

      this.dynamicUnits.push({
        unit: u,
        anchor,
        headerGroup,
        spawnBridge,
        joinBridge,
        leakHalo,
        backboneLine,
        backbonePositions,
      });
    }
  }

  private buildStateSpans(
    doc: SingleTraceDocument,
    anchors: Map<string, UnitAnchor>
  ): void {
    const unitMap = new Map<string, UnitRecord>(doc.units.map((u) => [u.id, u]));

    const matCache = new Map<string, THREE.MeshStandardMaterial>();
    for (const [key, vis] of Object.entries(STATE_VISUALS)) {
      matCache.set(
        key,
        new THREE.MeshStandardMaterial({
          color: vis.hex,
          emissive: vis.emissive,
          emissiveIntensity:
            key === 'running_cpu' || key === 'blocking_loop' ? 0.55 : 0.3,
          roughness: 0.32,
          metalness: 0.12,
        })
      );
    }

    for (const sp of doc.spans) {
      const anchor = anchors.get(sp.unit_id);
      if (!anchor || sp.end_ns <= sp.start_ns) continue;
      const vis = STATE_VISUALS[sp.state] ?? STATE_VISUALS.suspended_await;
      const mat = matCache.get(sp.state)!;

      const mesh = new THREE.Mesh(this.unitCylGeo, mat);
      mesh.scale.set(vis.radius, 0.001, vis.radius);
      const uName = unitMap.get(sp.unit_id)?.name ?? sp.unit_id;
      const durMs = ((sp.end_ns - sp.start_ns) / 1e6).toFixed(2);
      const stateDetailLabel =
        sp.detail === 'loop_stalled'
          ? 'Paused at await (I/O Ready — Waiting for Loop!)'
          : sp.detail === 'gil_reacquire'
            ? 'Waiting for GIL (I/O Done)'
            : vis.label;
      mesh.userData = {
        inspectTitle: `${uName} · ${stateDetailLabel}`,
        inspectSubtitle: `${(sp.start_ns / 1e6).toFixed(1)}ms → ${(sp.end_ns / 1e6).toFixed(1)}ms (${durMs}ms)`,
        accentColor: vis.color,
      };

      this.traceGroup.add(mesh);
      this.pickables.push(mesh);
      this.dynamicSpans.push({
        mesh,
        state: sp.state,
        startNs: sp.start_ns,
        endNs: sp.end_ns,
        x: anchor.x,
        z: anchor.z,
      });
    }
  }

  private buildCommArrows(
    doc: SingleTraceDocument,
    anchors: Map<string, UnitAnchor>
  ): void {
    const unitMap = new Map<string, UnitRecord>(doc.units.map((u) => [u.id, u]));
    const minVisualFlightNs = Math.max(
      1_500_000,
      Math.floor(this.referenceDurationNs * 0.042)
    );
    const allowSprites = doc.events.length <= 120;

    for (const ev of doc.events) {
      const fromA = anchors.get(ev.from_unit);
      const toA = anchors.get(ev.to_unit);
      if (!fromA || !toA || ev.from_unit === ev.to_unit) continue;

      const vis = EVENT_VISUALS[ev.kind] ?? EVENT_VISUALS.queue_message;
      const sendY = this.nsToY(ev.send_ns);
      const recvY = this.nsToY(ev.recv_ns);
      const slopeStartY = Math.abs(sendY - recvY) > 1.2 ? sendY : recvY;

      const startPos = new THREE.Vector3(fromA.x, slopeStartY, fromA.z);
      const endPos = new THREE.Vector3(toA.x, recvY, toA.z);
      const delta = new THREE.Vector3().subVectors(endPos, startPos);
      const totalLen = delta.length();
      if (totalLen < 0.25) continue;
      const dirNorm = delta.clone().normalize();

      const arrowMat = new THREE.MeshStandardMaterial({
        color: vis.hex,
        emissive: vis.hex,
        emissiveIntensity: 0.55,
        roughness: 0.25,
      });

      const shaftMesh = new THREE.Mesh(this.unitCylGeo, arrowMat);
      const headMesh = new THREE.Mesh(this.unitConeGeo, arrowMat);
      const pulseSphere = new THREE.Mesh(
        this.unitSphereGeo,
        new THREE.MeshBasicMaterial({ color: 0xffffff })
      );
      pulseSphere.scale.setScalar(0.62);

      const fromName = unitMap.get(ev.from_unit)?.name ?? ev.from_unit;
      const toName = unitMap.get(ev.to_unit)?.name ?? ev.to_unit;
      const metaData = {
        inspectTitle: `${vis.label}: ${fromName} → ${toName}`,
        inspectSubtitle: `Value "${ev.value}" at ${(ev.recv_ns / 1e6).toFixed(1)}ms`,
        accentColor: vis.color,
      };
      shaftMesh.userData = metaData;
      headMesh.userData = metaData;

      this.traceGroup.add(shaftMesh);
      this.traceGroup.add(headMesh);
      this.traceGroup.add(pulseSphere);
      this.pickables.push(shaftMesh, headMesh);

      let labelSprite: THREE.Sprite | null = null;
      if (allowSprites && ev.value) {
        labelSprite = createTextSprite(ev.value, {
          bgColor: 'rgba(15, 23, 42, 0.9)',
          borderColor: vis.color,
          textColor: vis.color,
          fontSize: 22,
          worldScale: 0.042,
        });
        const mid = new THREE.Vector3()
          .addVectors(startPos, endPos)
          .multiplyScalar(0.5);
        mid.y += 1.35;
        labelSprite.position.copy(mid);
        this.traceGroup.add(labelSprite);
      }

      const actualDur = Math.max(0, ev.recv_ns - ev.send_ns);
      const flightStartNs =
        actualDur >= minVisualFlightNs
          ? ev.send_ns
          : Math.max(0, ev.recv_ns - minVisualFlightNs * 0.5);
      const flightEndNs = Math.max(flightStartNs + minVisualFlightNs, ev.recv_ns);

      this.dynamicArrows.push({
        event: ev,
        flightStartNs,
        flightEndNs,
        startPos,
        endPos,
        dirNorm,
        totalLen,
        shaftMesh,
        headMesh,
        pulseSphere,
        labelSprite,
      });
    }
  }

  public setCurrentTimeNs(tNs: number): void {
    this.currentTimeNs = Math.max(0, tNs);
    const curY = this.nsToY(this.currentTimeNs);
    this.scanPlaneMesh.position.y = curY;
    this.scanPlaneMesh.visible =
      this.trace !== null &&
      this.currentTimeNs > 0 &&
      this.currentTimeNs < this.trace.metadata.duration_ns;

    for (const du of this.dynamicUnits) {
      const started = this.currentTimeNs >= du.unit.start_ns;
      const finished = this.currentTimeNs >= du.unit.end_ns;
      du.headerGroup.visible = started;
      if (du.spawnBridge) du.spawnBridge.visible = started;
      if (du.joinBridge) du.joinBridge.visible = finished;
      if (du.leakHalo) du.leakHalo.visible = finished;

      du.backboneLine.visible = started;
      if (started) {
        const clampedNs = Math.min(this.currentTimeNs, du.unit.end_ns);
        du.backbonePositions[4] = this.nsToY(clampedNs);
        du.backboneLine.geometry.attributes.position.needsUpdate = true;
      }
    }

    for (const ds of this.dynamicSpans) {
      if (this.currentTimeNs <= ds.startNs) {
        ds.mesh.visible = false;
        continue;
      }
      const visEndNs = Math.min(this.currentTimeNs, ds.endNs);
      const yTop = this.nsToY(ds.startNs);
      const yBot = this.nsToY(visEndNs);
      const height = Math.max(0.08, yTop - yBot);
      ds.mesh.visible = true;
      ds.mesh.position.set(ds.x, (yTop + yBot) * 0.5, ds.z);
      ds.mesh.scale.y = height;
    }

    const upAxis = new THREE.Vector3(0, 1, 0);
    const quat = new THREE.Quaternion();

    for (const da of this.dynamicArrows) {
      if (this.currentTimeNs < da.flightStartNs) {
        da.shaftMesh.visible = false;
        da.headMesh.visible = false;
        da.pulseSphere.visible = false;
        if (da.labelSprite) da.labelSprite.visible = false;
        continue;
      }

      const rawProgress =
        (this.currentTimeNs - da.flightStartNs) /
        Math.max(1, da.flightEndNs - da.flightStartNs);
      const progress = Math.max(0.04, Math.min(1, rawProgress));
      const eased = 1 - Math.pow(1 - progress, 3);

      const curLen = da.totalLen * eased;
      const headLen = Math.min(2.3, curLen * 0.42);
      const shaftLen = Math.max(0.05, curLen - headLen);

      quat.setFromUnitVectors(upAxis, da.dirNorm);

      const shaftCenter = da.startPos
        .clone()
        .addScaledVector(da.dirNorm, shaftLen * 0.5);
      da.shaftMesh.visible = true;
      da.shaftMesh.position.copy(shaftCenter);
      da.shaftMesh.quaternion.copy(quat);
      da.shaftMesh.scale.set(0.18, shaftLen, 0.18);

      const headCenter = da.startPos
        .clone()
        .addScaledVector(da.dirNorm, shaftLen + headLen * 0.5);
      da.headMesh.visible = true;
      da.headMesh.position.copy(headCenter);
      da.headMesh.quaternion.copy(quat);
      da.headMesh.scale.set(0.62, headLen, 0.62);

      if (progress < 0.995) {
        da.pulseSphere.visible = true;
        da.pulseSphere.position
          .copy(da.startPos)
          .addScaledVector(da.dirNorm, curLen);
      } else {
        da.pulseSphere.visible = false;
      }

      if (da.labelSprite) {
        da.labelSprite.visible = this.showValueLabels && progress > 0.35;
      }
    }
  }

  public renderFrame(timeSec: number): void {
    if (this.disposed) return;

    if (this.targetCamPos && this.targetLookAt) {
      this.camera.position.lerp(this.targetCamPos, 0.14);
      this.controls.target.lerp(this.targetLookAt, 0.14);
      if (this.camera.position.distanceTo(this.targetCamPos) < 0.25) {
        this.camera.position.copy(this.targetCamPos);
        this.controls.target.copy(this.targetLookAt);
        this.targetCamPos = null;
        this.targetLookAt = null;
      }
    }

    const pulse = 1 + 0.14 * Math.sin(timeSec * 6.0);
    for (const du of this.dynamicUnits) {
      if (du.leakHalo && du.leakHalo.visible) {
        du.leakHalo.scale.setScalar(pulse);
      }
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  private handlePointerMove = (ev: PointerEvent): void => {
    if (!this.onHoverCallback || this.pickables.length === 0) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointerNdc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointerNdc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.pointerNdc, this.camera);
    const hits = this.raycaster.intersectObjects(this.pickables, false);
    const visibleHit = hits.find((h) => h.object.visible && h.object.userData?.inspectTitle);
    if (visibleHit) {
      const ud = visibleHit.object.userData;
      this.onHoverCallback({
        title: ud.inspectTitle,
        subtitle: ud.inspectSubtitle,
        accentColor: ud.accentColor,
        screenX: ev.clientX,
        screenY: ev.clientY,
      });
    } else {
      this.onHoverCallback(null);
    }
  };

  private handlePointerLeave = (): void => {
    this.onHoverCallback?.(null);
  };

  public handleResize(): void {
    if (this.disposed) return;
    this.syncRendererSize();
    if (this.trace && !this.userManipulatedCamera) {
      this.setCameraPreset(this.currentPreset, true);
    }
  }

  private clearTraceGroup(): void {
    for (let i = this.traceGroup.children.length - 1; i >= 0; i--) {
      const obj = this.traceGroup.children[i];
      this.traceGroup.remove(obj);
      this.disposeObjectResources(obj);
    }
    this.dynamicSpans = [];
    this.dynamicUnits = [];
    this.dynamicArrows = [];
    this.pickables = [];
  }

  private disposeObjectResources(obj: THREE.Object3D): void {
    obj.traverse((child) => {
      if (child instanceof THREE.Line) {
        child.geometry.dispose();
        if (child.material instanceof THREE.Material) child.material.dispose();
      } else if (child instanceof THREE.Sprite) {
        if (child.material.map) child.material.map.dispose();
        child.material.dispose();
      } else if (child instanceof THREE.Mesh) {
        if (child.material instanceof THREE.Material) {
          child.material.dispose();
        }
      }
    });
  }

  public dispose(): void {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.renderer.domElement.removeEventListener('pointerdown', this.markUserCameraInput);
    this.renderer.domElement.removeEventListener('wheel', this.markUserCameraInput);
    this.renderer.domElement.removeEventListener('pointermove', this.handlePointerMove);
    this.renderer.domElement.removeEventListener('pointerleave', this.handlePointerLeave);
    this.clearTraceGroup();
    this.unitCylGeo.dispose();
    this.unitConeGeo.dispose();
    this.unitSphereGeo.dispose();
    this.unitTorusGeo.dispose();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
