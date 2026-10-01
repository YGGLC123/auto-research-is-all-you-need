import {
  Background,
  BackgroundVariant,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { MapModel } from "@/data";
import { useT } from "@/i18n";
import { place, type Placed } from "@/layout";
import { ClaimCard, RootPlate, SectionChip } from "./cards";
import { Button, Tip } from "./ui";

type NodeData = {
  placed: Placed;
  selected: boolean;
  dimmed: boolean;
  hit: boolean;
  collapsed: boolean;
  onToggle: (id: string) => void;
};
type MapFlowNode = Node<NodeData>;

const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function RootNode({ data }: NodeProps<MapFlowNode>) {
  return (
    <>
      <RootPlate node={data.placed.node} selected={data.selected} dimmed={data.dimmed} />
      <Handle type="source" position={Position.Right} />
    </>
  );
}

function SectionNode({ data }: NodeProps<MapFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <SectionChip
        node={data.placed.node}
        branch={data.placed.branch}
        collapsed={data.collapsed}
        onToggle={() => data.onToggle(data.placed.node.id)}
        selected={data.selected}
        dimmed={data.dimmed}
      />
      <Handle type="source" position={Position.Right} />
    </>
  );
}

function ClaimNode({ data }: NodeProps<MapFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <ClaimCard node={data.placed.node} branch={data.placed.branch} selected={data.selected} dimmed={data.dimmed} hit={data.hit} />
      <Handle type="source" position={Position.Right} />
    </>
  );
}

const nodeTypes = { root: RootNode, section: SectionNode, claim: ClaimNode };

type Props = {
  model: MapModel;
  collapsed: Set<string>;
  onToggle: (id: string) => void;
  selected: string | null;
  onSelect: (id: string | null) => void;
  isDimmed: (id: string) => boolean;
  isHit: (id: string) => boolean;
  remPx: number;
  focusTick: number;
};

function Canvas({ model, collapsed, onToggle, selected, onSelect, isDimmed, isHit, remPx, focusTick }: Props) {
  const t = useT();
  const rf = useReactFlow();
  const placed = useMemo(() => place(model, collapsed, remPx), [model, collapsed, remPx]);
  const ids = useMemo(() => new Set(placed.map((p) => p.node.id)), [placed]);

  const nodes: MapFlowNode[] = placed.map((p) => ({
    id: p.node.id,
    type: p.kind,
    position: { x: p.x, y: p.y },
    width: p.w,
    height: p.h,
    draggable: false,
    connectable: false,
    data: {
      placed: p,
      selected: selected === p.node.id,
      dimmed: isDimmed(p.node.id),
      hit: isHit(p.node.id),
      collapsed: collapsed.has(p.node.id),
      onToggle,
    },
  }));

  const edges: Edge[] = placed
    .filter((p) => p.node.parent && ids.has(p.node.parent))
    .map((p) => ({
      id: `e-${p.node.id}`,
      source: p.node.parent!,
      target: p.node.id,
      style: { stroke: p.branch, strokeOpacity: isDimmed(p.node.id) ? 0.18 : 0.6, strokeWidth: 1.6 },
    }));
  // Where a moved card came from: a dashed blue-pencil trace back to its old section.
  for (const p of placed) {
    const from = p.node.change?.moved_from?.replace(/…$/, "");
    if (!from) continue;
    const old = model.nodes.find((n) => n.id !== p.node.parent && n.title.startsWith(from));
    if (old && ids.has(old.id)) {
      edges.push({
        id: `moved-${p.node.id}`,
        source: old.id,
        target: p.node.id,
        style: { stroke: "var(--pencil)", strokeDasharray: "5 5", strokeWidth: 1.4, strokeOpacity: isDimmed(p.node.id) ? 0.2 : 0.8 },
      });
    }
  }

  const fitted = useRef("");
  useEffect(() => {
    const key = `${placed.length}:${remPx}`;
    if (fitted.current === key) return;
    const first = fitted.current === "";
    fitted.current = key;
    requestAnimationFrame(() => {
      // opened on a deep link: start on that card, not on the whole tree
      const target = first && selected ? placed.find((p) => p.node.id === selected) : undefined;
      if (target) rf.setCenter(target.x + target.w / 2, target.y + target.h / 2, { zoom: 1, duration: 0 });
      else rf.fitView({ padding: 0.1, duration: first || reduceMotion() ? 0 : 250 });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placed.length, remPx, rf]);

  useEffect(() => {
    if (!selected || !focusTick) return;
    const p = placed.find((x) => x.node.id === selected);
    if (!p) return;
    rf.setCenter(p.x + p.w / 2, p.y + p.h / 2, { zoom: Math.max(rf.getZoom(), 0.85), duration: reduceMotion() ? 0 : 450 });
    // only when the selection comes from outside the canvas (receipt, search, deep link)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTick]);

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        minZoom={0.2}
        maxZoom={2.2}
        attributionPosition="top-right"
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} />
        {/* a minimap only earns its space on big trees; on small ones it hides cards */}
        {placed.length > 40 && (
          <MiniMap
            pannable
            zoomable
            className="!hidden !rounded-xl !border !border-line !shadow-card lg:!block"
            nodeColor={(n) => {
              const d = (n as MapFlowNode).data;
              return d.placed.node.badges.lever ? "#d92b1f" : d.placed.node.badges.taste ? "#ffe03d" : d.placed.branch;
            }}
            nodeBorderRadius={6}
          />
        )}
      </ReactFlow>
      <div className="absolute bottom-4 left-4 flex items-center gap-0.5 rounded-xl border border-line bg-surface p-1 shadow-card">
        <Tip label={t("zoom_out")} side="top">
          <Button variant="ghost" size="icon" onClick={() => rf.zoomOut({ duration: 200 })} aria-label={t("zoom_out")}>
            <ZoomOut className="h-4 w-4" />
          </Button>
        </Tip>
        <Tip label={t("zoom_in")} side="top">
          <Button variant="ghost" size="icon" onClick={() => rf.zoomIn({ duration: 200 })} aria-label={t("zoom_in")}>
            <ZoomIn className="h-4 w-4" />
          </Button>
        </Tip>
        <Tip label={t("fit")} side="top">
          <Button variant="ghost" size="icon" onClick={() => rf.fitView({ padding: 0.1, duration: 300 })} aria-label={t("fit")}>
            <Maximize2 className="h-4 w-4" />
          </Button>
        </Tip>
      </div>
    </div>
  );
}

export function MapCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
