import { X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hasChange, needsYou, type MapModel, type MapNode } from "./data";
import { LangContext, translate, type Lang } from "./i18n";
import { Header, type Filter } from "./components/Header";
import { Inspector } from "./components/Inspector";
import { MapCanvas } from "./components/MapCanvas";
import { Receipt } from "./components/Receipt";
import { Button, TooltipProvider } from "./components/ui";

const SCALES = [0.875, 1, 1.125, 1.25, 1.5, 1.75];

function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private window or blocked storage: the preference just isn't remembered */
  }
}

function initialScale(): number {
  const fromUrl = Number(new URLSearchParams(location.search).get("scale"));
  if (fromUrl >= 0.75 && fromUrl <= 2.5) return fromUrl;
  const saved = Number(readPref("ar-map-scale"));
  return SCALES.includes(saved) ? saved : 1;
}

function hashNode(model: MapModel): string | null {
  const m = (location.hash || "").match(/node=([^&]+)/);
  if (!m) return null;
  const id = decodeURIComponent(m[1]);
  return model.nodes.some((n) => n.id === id) ? id : null;
}

/** Big trees open two levels deep, plus every path that leads to a change. */
function initialCollapsed(model: MapModel): Set<string> {
  if (model.nodes.length <= 60) return new Set();
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  const keepOpen = new Set<string>();
  for (const n of model.nodes) {
    if (!hasChange(n) && !needsYou(n)) continue;
    let p = n.parent;
    while (p) {
      keepOpen.add(p);
      p = byId.get(p)?.parent ?? null;
    }
  }
  return new Set(model.nodes.filter((n) => n.depth >= 2 && n.children.length && !keepOpen.has(n.id)).map((n) => n.id));
}

function matches(n: MapNode, q: string): boolean {
  if (!q) return false;
  const s = q.toLowerCase();
  return n.id.toLowerCase() === s || n.title.toLowerCase().includes(s) || (n.statement || "").toLowerCase().includes(s);
}

export function App({ model }: { model: MapModel }) {
  const [lang, setLang] = useState<Lang>(() => {
    const saved = readPref("ar-map-lang");
    return saved === "zh" || saved === "en" ? saved : model.lang === "en" ? "en" : "zh";
  });
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const [scale, setScale] = useState(initialScale);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(() => hashNode(model));
  const [focusTick, setFocusTick] = useState(() => (hashNode(model) ? 1 : 0));
  const [collapsed, setCollapsed] = useState<Set<string>>(() => initialCollapsed(model));
  const [receiptOpen, setReceiptOpen] = useState(false);

  const byId = useMemo(() => new Map(model.nodes.map((n) => [n.id, n])), [model]);

  useEffect(() => {
    document.documentElement.style.setProperty("--ui-scale", String(scale));
  }, [scale]);
  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.title = `${translate(lang, "app")} — ${model.project}`;
  }, [lang, model.project]);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const select = useCallback(
    (id: string | null, fromOutside = false) => {
      setSelected(id);
      if (id) {
        // reveal a card hidden inside a collapsed branch
        setCollapsed((prev) => {
          let p = byId.get(id)?.parent ?? null;
          let next: Set<string> | null = null;
          while (p) {
            if (prev.has(p)) {
              next = next ?? new Set(prev);
              next.delete(p);
            }
            p = byId.get(p)?.parent ?? null;
          }
          return next ?? prev;
        });
        if (fromOutside) setFocusTick((x) => x + 1);
      }
      try {
        history.replaceState(null, "", id ? `#node=${encodeURIComponent(id)}` : location.pathname + location.search);
      } catch {
        /* file:// in some browsers refuses replaceState; the page still works */
      }
    },
    [byId],
  );

  // keep ancestors of matching cards lit, so the path to them stays readable
  const lit = useMemo(() => {
    const want = (n: MapNode) =>
      query ? matches(n, query) : filter === "changed" ? hasChange(n) : filter === "needs" ? needsYou(n) : true;
    if (!query && filter === "all") return null;
    const on = new Set<string>();
    for (const n of model.nodes) {
      if (!want(n)) continue;
      on.add(n.id);
      let p = n.parent;
      while (p) {
        on.add(p);
        p = byId.get(p)?.parent ?? null;
      }
    }
    return on;
  }, [model, byId, filter, query]);

  const isDimmed = useCallback((id: string) => (lit ? !lit.has(id) : false), [lit]);
  const isHit = useCallback((id: string) => Boolean(query) && matches(byId.get(id)!, query), [byId, query]);

  const changedCount = model.nodes.filter(hasChange).length;
  const needsCount = model.nodes.filter(needsYou).length;
  const node = selected ? byId.get(selected) ?? null : null;

  const pick = (id: string) => {
    select(id, true);
    setReceiptOpen(false);
  };

  return (
    <LangContext.Provider value={lang}>
      <TooltipProvider>
        <div className="grid h-full grid-rows-[auto_1fr] bg-bg text-ink">
          <Header
            model={model}
            filter={filter}
            onFilter={setFilter}
            changedCount={changedCount}
            needsCount={needsCount}
            query={query}
            onQuery={setQuery}
            dark={dark}
            onDark={() => {
              const next = !dark;
              setDark(next);
              writePref("ar-map-theme", next ? "dark" : "light");
            }}
            onLang={() => {
              const next = lang === "zh" ? "en" : "zh";
              setLang(next);
              writePref("ar-map-lang", next);
            }}
            onScale={(step) => {
              const i = SCALES.findIndex((s) => s >= scale);
              const next = SCALES[Math.min(SCALES.length - 1, Math.max(0, (i < 0 ? 1 : i) + step))];
              setScale(next);
              writePref("ar-map-scale", String(next));
            }}
            onReceipt={() => setReceiptOpen((v) => !v)}
          />
          <div className="relative grid min-h-0 grid-rows-[minmax(0,1fr)_auto] xl:grid-cols-[21rem_minmax(0,1fr)_25rem] xl:grid-rows-1 lg:grid-cols-[minmax(0,1fr)_24rem] lg:grid-rows-1">
            <aside className="hidden min-h-0 overflow-y-auto border-r border-line bg-bg p-4 xl:block">
              <Receipt model={model} onPick={pick} />
            </aside>
            <main className="relative min-h-0 bg-canvas">
              <MapCanvas
                model={model}
                collapsed={collapsed}
                onToggle={(id) =>
                  setCollapsed((prev) => {
                    const next = new Set(prev);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  })
                }
                selected={selected}
                onSelect={(id) => select(id)}
                isDimmed={isDimmed}
                isHit={isHit}
                remPx={16 * scale}
                focusTick={focusTick}
              />
            </main>
            <aside
              className={
                "min-h-0 border-t border-line bg-surface lg:block lg:border-l lg:border-t-0 " +
                (node ? "max-h-[48vh] lg:max-h-none" : "hidden")
              }
            >
              <Inspector model={model} node={node} onPick={pick} onClose={() => select(null)} />
            </aside>

            {receiptOpen && (
              <div className="absolute inset-0 z-40 flex xl:hidden" role="dialog" aria-label={translate(lang, "receipt_title")}>
                <div className="h-full w-[min(22rem,92vw)] overflow-y-auto border-r border-line bg-bg p-4 shadow-card">
                  <div className="mb-2 flex justify-end">
                    <Button variant="ghost" size="sm" onClick={() => setReceiptOpen(false)}>
                      <X className="h-4 w-4" />
                      {translate(lang, "receipt_close")}
                    </Button>
                  </div>
                  <Receipt model={model} onPick={pick} />
                </div>
                <button type="button" aria-label={translate(lang, "receipt_close")} className="flex-1 bg-ink/20" onClick={() => setReceiptOpen(false)} />
              </div>
            )}
          </div>
        </div>
      </TooltipProvider>
    </LangContext.Provider>
  );
}
