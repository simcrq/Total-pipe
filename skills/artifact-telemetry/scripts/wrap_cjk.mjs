// Width-constrained line breaking for Chinese research-slide prose.
// Measurement is injected: callers must use the actual selected font/size.
const NO_START = /^[，。！？；：、）》】」』〉〕］｝)\],.!?;:%‰℃°]/u;
const NO_END = /[（《【「『〈〔［｛(]$/u;
const ATOM = /(?:\d+(?:\.\d+)?\s*(?:nm|µm|μm|mm|cm|mW|kW|GHz|MHz|Hz|ms|ns|ps|eV|keV|K|Å|%)(?![A-Za-z]))|(?:[A-Za-z0-9\u0370-\u03ff\u2070-\u209f]+(?:[./_+−-][A-Za-z0-9\u0370-\u03ff\u2070-\u209f]+)*)|(?:\s+)|(?:[^\s])/gu;

export function wrapCjk(text, { widthPx, measureText, safetyPx = 4 } = {}) {
  if (typeof text !== 'string' || !Number.isFinite(widthPx) || widthPx <= safetyPx ||
      !Number.isFinite(safetyPx) || safetyPx < 0 || typeof measureText !== 'function') {
    throw new TypeError('wrapCjk needs text, positive widthPx and a font-specific measureText');
  }
  const limit = widthPx - safetyPx;
  const measure = s => {
    const w = measureText(s);
    if (!Number.isFinite(w) || w < 0) throw new TypeError('Invalid measured text width');
    return w;
  };
  return text.split('\n').map(paragraph => {
    if (!paragraph.trim()) return paragraph;
    // Grapheme segmentation preserves emoji/combining sequences; the regex
    // groups Latin words, formula-like tokens and common number-unit pairs.
    const graphemes = new Intl.Segmenter('zh', {granularity:'grapheme'});
    const tokens = [];
    // Rejoin boundaries inside grapheme clusters (e.g. ZWJ emoji).
    const boundaries = new Set();
    for (const g of graphemes.segment(paragraph)) boundaries.add(g.index + g.segment.length);
    let offset = 0, pending = '';
    for (const match of paragraph.matchAll(ATOM)) {
      pending += match[0]; offset += match[0].length;
      if (boundaries.has(offset)) { tokens.push(pending); pending = ''; }
    }
    if (pending) tokens.push(pending);
    const wordEnds = new Set([...new Intl.Segmenter('zh', {granularity:'word'}).segment(paragraph)]
      .map(segment => segment.index + segment.segment.length));
    const offsets = [0];
    for (const token of tokens) offsets.push(offsets.at(-1) + token.length);
    if (tokens.length > 1500) throw new RangeError('Split oversized slide prose before wrapping');
    const n = tokens.length;
    const cost = Array(n+1).fill(Infinity), next = Array(n).fill(-1);
    cost[n] = 0;
    for (let i=n-1;i>=0;i--) {
      let candidate = '';
      for (let j=i;j<n;j++) {
        candidate += tokens[j];
        const line = candidate.trim();
        const width = measure(line);
        if (width > limit) break;
        if (!line || NO_END.test(line)) continue;
        const rest = tokens.slice(j+1).join('').trimStart();
        if (rest && NO_START.test(rest)) continue;
        if (!Number.isFinite(cost[j+1])) continue;
        // Balance short final lines as well, with a smaller final-line weight.
        const slack = (limit-width)/limit;
        const wordPenalty = wordEnds.has(offsets[j+1]) ? 0 : 0.25;
        const score = 1 + slack*slack*(j===n-1 ? 0.6 : 1) + wordPenalty + cost[j+1];
        if (score < cost[i]) { cost[i]=score; next[i]=j+1; }
      }
    }
    if (next[0] < 0) throw new RangeError('Unbreakable text exceeds list width; widen or rephrase instead of shrinking');
    const lines = [];
    for (let i=0;i<n;) {
      const end=next[i];
      lines.push(tokens.slice(i,end).join('').trim());
      i=end;
    }
    return lines.join('\n');
  }).join('\n');
}

export function canvasMeasurer(canvasModule, {typeface, fontSizePx, bold = false}) {
  const context = new canvasModule.Canvas(1,1).getContext('2d');
  context.font = `${bold ? 'bold ' : ''}${fontSizePx}px "${typeface.replaceAll('"','\\"')}"`;
  return text => context.measureText(text).width;
}
