// The research map model, as research_map.py builds it (schema auto-research/research-map-v1).

export type Badges = {
  new: boolean;
  changed: boolean;
  moved: boolean;
  refute: number;
  conflict: boolean;
  support: number;
  figure: number;
  no_evidence: boolean;
  no_figure: boolean;
  lever: boolean;
  live_bet: boolean;
  signed: boolean;
  taste: number;
};

export type Commit = {
  id: string;
  at: string;
  at_full?: string;
  kind: string;
  cause: string;
  by: string;
  trigger: string | null;
  message: string;
  counterfactual: string | null;
  nodes?: string[];
  ops?: { op: string; id: string }[];
};

export type TasteHit = {
  rule: string;
  rule_text: string;
  quote?: string | null;
  node: string;
  title: string;
  field: string;
  match: string;
  excerpt: string;
};

export type NodeState = { epistemic?: string; narrative?: string };

export type Change = {
  kinds: string[];
  fields: string[];
  commits: Commit[];
  before?: { title?: string; summary?: string; statement?: string; state?: NodeState };
  after?: { title?: string; summary?: string; statement?: string; state?: NodeState };
  moved_from?: string;
};

export type Lever = {
  event: string;
  node: string;
  title: string;
  roles_held: string[];
  demoted_commit: string;
  demoted_at: string;
  cause: string;
  replacement: string | null;
};

export type MapNode = {
  id: string;
  title: string;
  depth: number;
  parent: string | null;
  children: string[];
  type: string;
  epistemic: string | null;
  narrative: string | null;
  roles: string[];
  summary: string;
  statement: string;
  badges: Badges;
  change: Change | null;
  evidence: { supports: string[]; refutes: string[]; figures: string[]; conflict: boolean } | null;
  taste: TasteHit[];
  signed: string[];
  lever: Lever | null;
  live_bet: { resolution_condition?: string | null } | null;
};

export type Dropped = {
  id: string;
  title: string;
  disposition: string | null;
  reason: string | null;
  basis_refs: string[];
  commit: Commit | null;
};

export type MapModel = {
  schema: string;
  built_at: string;
  lang: "zh" | "en";
  project: string;
  ref: string;
  head: string;
  head_at: string;
  head_age_days: number;
  since: { commit: string | null; at: string | null; source: string };
  root: string;
  nodes: MapNode[];
  dropped: Dropped[];
  log?: Commit[];
  counts: Record<string, number>;
  taste_rules: number;
  reminders: unknown[];
  notes: string[];
  staleness: { count: number; partial: boolean; files: { path: string; modified: string }[] };
  tz_offset_minutes?: number | null;
};

// research_map.py replaces the placeholder in #map-data with the model JSON. The
// bundle must not spell the placeholder itself (Python replaces every occurrence),
// so an unfilled page is recognised by its content not being a JSON object.
export function readEmbeddedModel(): MapModel | null {
  const raw = document.getElementById("map-data")?.textContent?.trim() ?? "";
  if (!raw.startsWith("{")) return null;
  return JSON.parse(raw) as MapModel;
}

export async function loadModel(): Promise<MapModel> {
  const embedded = readEmbeddedModel();
  if (embedded) return embedded;
  if (import.meta.env.DEV) {
    const wantEnglish = new URLSearchParams(location.search).get("demo") === "en";
    const mod = wantEnglish ? await import("../sample/demo-en.json") : await import("../sample/demo-zh.json");
    return mod.default as unknown as MapModel;
  }
  throw new Error("This page has no map data. Render it with research_map.py.");
}

/** What needs a human decision on this node. */
export function needsYou(n: MapNode): boolean {
  return n.badges.lever || n.badges.taste > 0 || n.badges.refute > 0 || n.badges.conflict;
}

export function hasChange(n: MapNode): boolean {
  return n.badges.new || n.badges.changed || n.badges.moved;
}
