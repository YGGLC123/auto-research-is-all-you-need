// Word-level redline for Latin text, character-level for CJK. Statements are short
// (a few hundred characters at most), so a plain LCS table is fast enough.

export type Seg = { kind: "same" | "del" | "ins"; text: string };

const TOKEN = /[㐀-鿿豈-﫿]|[　-〿＀-￯]|[A-Za-z0-9.,%'’\-]+|\s+|./gu;

function tokens(text: string): string[] {
  return text.match(TOKEN) ?? [];
}

export function redline(before: string, after: string): Seg[] {
  const a = tokens(before);
  const b = tokens(after);
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: Seg[] = [];
  const push = (kind: Seg["kind"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      push("del", a[i++]);
    } else {
      push("ins", b[j++]);
    }
  }
  while (i < n) push("del", a[i++]);
  while (j < m) push("ins", b[j++]);
  return coalesce(out.map((s) => (s.kind !== "same" && !s.text.trim() ? { kind: "same", text: s.text } : s)));
}

/**
 * Interleaved word edits ("When|Rebuttals authors|that") are unreadable. Merge each
 * stretch of edits -- including the spaces and single characters caught between
 * them -- into one deletion followed by one insertion.
 */
function coalesce(segs: Seg[]): Seg[] {
  const out: Seg[] = [];
  let k = 0;
  while (k < segs.length) {
    if (segs[k].kind === "same") {
      out.push(segs[k++]);
      continue;
    }
    let del = "";
    let ins = "";
    while (k < segs.length) {
      const s = segs[k];
      if (s.kind === "del") del += s.text;
      else if (s.kind === "ins") ins += s.text;
      else {
        const nextIsEdit = k + 1 < segs.length && segs[k + 1].kind !== "same";
        if (!nextIsEdit || s.text.trim().length > 1) break;
        del += s.text;
        ins += s.text;
      }
      k++;
    }
    if (del.trim()) out.push({ kind: "del", text: del });
    if (ins.trim()) out.push({ kind: "ins", text: ins });
  }
  return out;
}

/** Share of the new text that is new; above ~0.55 a sentence reads better as a whole replacement. */
export function churn(segs: Seg[]): number {
  const total = segs.reduce((s, x) => s + (x.kind === "del" ? 0 : x.text.length), 0) || 1;
  return segs.reduce((s, x) => s + (x.kind === "ins" ? x.text.length : 0), 0) / total;
}

/** Split `text` around `match` (case-insensitive) so the hit can be highlighted. */
export function splitMatch(text: string, match: string): [string, string, string] | null {
  if (!match) return null;
  const at = text.toLowerCase().indexOf(match.toLowerCase());
  if (at < 0) return null;
  return [text.slice(0, at), text.slice(at, at + match.length), text.slice(at + match.length)];
}
