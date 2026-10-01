import { hierarchy, tree } from "d3-hierarchy";
import type { MapModel, MapNode } from "./data";

// Card sizes in rem, so the whole map scales with the reader's text size.
export const SIZE = {
  root: { w: 17, h: 6.75 },
  section: { w: 14.5, h: 3.4 },
  claim: { w: 19, h: 5.5 },
};
const COL_GAP = 4.25; // rem between columns
const ROW_GAP = 0.9; // rem between sibling cards

export type Kind = "root" | "section" | "claim";

export function kindOf(n: MapNode, root: string): Kind {
  if (n.id === root) return "root";
  return n.type === "narrative" ? "section" : "claim";
}

// Muted branch hues: they only tint rails and edges, so the loud colours stay
// reserved for things that need the researcher.
export const BRANCH = ["#6b7fa8", "#5f9887", "#b0915e", "#94739c", "#6a8ea3", "#8d9a5a", "#a3746b"];

export type Placed = {
  node: MapNode;
  kind: Kind;
  x: number;
  y: number;
  w: number;
  h: number;
  branch: string;
};

export function place(model: MapModel, collapsed: Set<string>, remPx: number): Placed[] {
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  type T = { id: string; children?: T[] };
  const build = (id: string): T => {
    const n = byId.get(id)!;
    const kids = collapsed.has(id) ? [] : n.children.filter((c) => byId.has(c));
    return { id, children: kids.length ? kids.map(build) : undefined };
  };
  const h = hierarchy(build(model.root));
  const rowOf = (id: string) => SIZE[kindOf(byId.get(id)!, model.root)].h + ROW_GAP;
  const slot = Math.max(SIZE.claim.h, SIZE.section.h) + ROW_GAP;
  tree<T>()
    .nodeSize([slot * remPx, 1])
    .separation((a, b) => {
      const need = (rowOf(a.data.id) + rowOf(b.data.id)) / 2 / slot;
      return a.parent === b.parent ? Math.max(need, 1) : Math.max(need, 1.2);
    })(h);

  // Column x: widths accumulate by depth, so every depth sits in one tidy column.
  const widthAt = (depth: number) =>
    depth === 0 ? SIZE.root.w : depth === 1 ? SIZE.section.w : SIZE.claim.w;
  const colX: number[] = [0];
  for (let d = 1; d < 12; d++) colX[d] = colX[d - 1] + widthAt(d - 1) + COL_GAP;

  const sectionOrder = (byId.get(model.root)?.children ?? []).filter((c) => byId.has(c));
  const branchOf = (id: string): string => {
    let cur = byId.get(id);
    while (cur && cur.parent && cur.parent !== model.root) cur = byId.get(cur.parent);
    const i = cur ? sectionOrder.indexOf(cur.id) : -1;
    return i < 0 ? "#8a909c" : BRANCH[i % BRANCH.length];
  };

  const out: Placed[] = [];
  h.each((d) => {
    const node = byId.get(d.data.id)!;
    const kind = kindOf(node, model.root);
    const size = SIZE[kind];
    out.push({
      node,
      kind,
      x: colX[d.depth] * remPx,
      y: (d.x ?? 0) - (size.h * remPx) / 2,
      w: size.w * remPx,
      h: size.h * remPx,
      branch: branchOf(node.id),
    });
  });
  return out;
}
