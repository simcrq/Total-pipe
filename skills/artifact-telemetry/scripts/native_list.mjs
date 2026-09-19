// Opt-in native lists. Geometry/fontSize are CSS px; all *Pt options are points.
// Kept independent of artifact-tool so authoring and twin rendering share it.
import { wrapCjk } from './wrap_cjk.mjs';
export function nativeListModel(items, options = {}) {
  if (!Array.isArray(items) || !items.length || items.some(x => typeof x !== 'string' || !x.trim() || /\r|\n\s*\n/u.test(x))) {
    throw new TypeError('items must contain nonempty strings without blank paragraphs');
  }
  const { fontSizePt = 18, minFontSizePt = 17, gapPt = 12,
    marginLeftPt = 18, hangingPt = 9 } = options;
  for (const [key, value] of Object.entries({ fontSizePt, minFontSizePt, gapPt, marginLeftPt, hangingPt })) {
    if (!Number.isFinite(value) || value < 0) throw new TypeError(`${key} must be a finite nonnegative number`);
  }
  if (fontSizePt <= 0 || fontSizePt < minFontSizePt) throw new RangeError('Font is below the requested point-size floor');
  if (hangingPt <= 0 || marginLeftPt < hangingPt) throw new RangeError('Invalid bullet indentation');
  return {
    style: { fontSize: fontSizePt * 96 / 72, autoFit: 'none', verticalAlignment: 'top', wrap: 'square' },
    paragraphs: items.map((text, i) => ({
      bulletCharacter: '•', marginLeft: Math.round(marginLeftPt * 12700),
      indent: -Math.round(hangingPt * 12700),
      spaceAfter: i === items.length - 1 ? 0 : Math.round(gapPt * 100),
      runs: [options.wrapWidthPx === undefined ? text : wrapCjk(text, {
        widthPx: options.wrapWidthPx - marginLeftPt * 96 / 72,
        measureText: options.measureText,
      })],
    })),
  };
}

export function applyNativeList(shape, items, options = {}) {
  const model = nativeListModel(items, options);
  shape.text = model.paragraphs;
  shape.text.style = model.style;
  return shape;
}

export function checkNativeListLayout(element, minimumPt = 17) {
  const issues = [];
  if (!Number.isFinite(element?.resolvedFontSize)) issues.push('FONT_SIZE_UNRESOLVED');
  else if (element.resolvedFontSize * 72 / 96 < minimumPt) issues.push('FONT_BELOW_POINT_FLOOR');
  const lines = element?.textLayout?.lines;
  if (!Array.isArray(lines)) issues.push('LIST_LINES_UNRESOLVED');
  else if (lines.some(line => /^\s*[•●▪]\s*$/u.test(line.text))) issues.push('ORPHAN_LIST_MARKER');
  if (element?.textLayout?.overflow === true) issues.push('LIST_TEXT_OVERFLOW');
  return issues;
}
