import { LayoutHint, UnitRecord } from '../types';

export interface UnitAnchor {
  id: string;
  x: number;
  z: number;
  angleRad: number;
  radius: number;
}

/**
 * Computes 2D XZ coordinates for every concurrent unit (time flows along -Y).
 * Inspired by divan's gothree.js radial hierarchy, enhanced with clean topology
 * modes for rings (Ping-Pong), pipelines (Prime Sieve), and multi-level trees.
 */
export function computeUnitLayout(
  units: UnitRecord[],
  hint: LayoutHint
): Map<string, UnitAnchor> {
  const anchors = new Map<string, UnitAnchor>();
  anchors.set('main', { id: 'main', x: 0, z: 0, angleRad: 0, radius: 0 });

  const childrenByParent = new Map<string, UnitRecord[]>();
  for (const u of units) {
    if (u.id === 'main') continue;
    const pid = u.parent_id && units.some((x) => x.id === u.parent_id) ? u.parent_id : 'main';
    const list = childrenByParent.get(pid) ?? [];
    list.push(u);
    childrenByParent.set(pid, list);
  }

  // Sort siblings deterministically by start_ns then id
  for (const list of childrenByParent.values()) {
    list.sort((a, b) => a.start_ns - b.start_ns || a.id.localeCompare(b.id));
  }

  const rootChildren = childrenByParent.get('main') ?? [];

  if (hint === 'pipeline' && rootChildren.length > 0) {
    // Arc layout (300 deg) around main so pipeline hops skirt the perimeter
    // while discovered primes shoot radially straight into central `main`!
    const n = rootChildren.length;
    const r = Math.max(26, Math.min(44, 18 + n * 2.2));
    const spanRad = Math.PI * 1.68;
    const startRad = -Math.PI * 0.84;
    rootChildren.forEach((child, i) => {
      const ang = n === 1 ? 0 : startRad + (spanRad * i) / (n - 1);
      anchors.set(child.id, {
        id: child.id,
        x: Math.cos(ang) * r,
        z: Math.sin(ang) * r,
        angleRad: ang,
        radius: r,
      });
    });
  } else {
    // BFS radial tree layout supporting arbitrary nesting depth
    const queue: string[] = ['main'];
    while (queue.length > 0) {
      const pid = queue.shift()!;
      const parentAnchor = anchors.get(pid) ?? {
        id: pid,
        x: 0,
        z: 0,
        angleRad: 0,
        radius: 0,
      };
      const kids = childrenByParent.get(pid) ?? [];
      if (kids.length === 0) continue;

      if (pid === 'main') {
        const n = kids.length;
        const hasGrandkids = kids.some((k) => (childrenByParent.get(k.id)?.length ?? 0) > 0);
        let r = 24;
        if (hasGrandkids || hint === 'tree') {
          r = n <= 2 ? 28 : 34;
        } else if (hint === 'ring') {
          r = n <= 3 ? 22 : Math.min(42, 18 + n * 1.6);
        } else {
          r = n === 1 ? 22 : Math.min(44, 20 + n * 1.35);
        }

        const startOffset = n === 2 ? 0 : -Math.PI / 2;
        kids.forEach((child, idx) => {
          const ang = startOffset + (2 * Math.PI * idx) / n;
          anchors.set(child.id, {
            id: child.id,
            x: Math.cos(ang) * r,
            z: Math.sin(ang) * r,
            angleRad: ang,
            radius: r,
          });
          queue.push(child.id);
        });
      } else {
        // Subworkers fan out in an outward sector / satellite ring around parent
        const n = kids.length;
        const subRadius = n <= 4 ? 13.5 : Math.min(22, 10 + n * 0.85);
        const baseAngle = parentAnchor.angleRad;
        const spread = n <= 4 ? Math.PI * 0.85 : Math.PI * 1.55;
        kids.forEach((child, idx) => {
          const offset = n === 1 ? 0 : -spread / 2 + (spread * idx) / (n - 1);
          const ang = baseAngle + offset;
          anchors.set(child.id, {
            id: child.id,
            x: parentAnchor.x + Math.cos(ang) * subRadius,
            z: parentAnchor.z + Math.sin(ang) * subRadius,
            angleRad: ang,
            radius: subRadius,
          });
          queue.push(child.id);
        });
      }
    }
  }

  // Fallback for any orphan child whose parent was not reachable from main
  for (const u of units) {
    if (!anchors.has(u.id)) {
      anchors.set(u.id, { id: u.id, x: 18, z: 18, angleRad: 0.78, radius: 25 });
    }
  }

  return anchors;
}
