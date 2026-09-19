import test from 'node:test';
import assert from 'node:assert/strict';
import { nativeListModel, checkNativeListLayout } from '../scripts/native_list.mjs';

test('native lists preserve source items and serialize point-based typography', () => {
  const items = ['SiO₂/Si 上的 2 nm MoS₂', '其他材料', '定量结果'];
  const model = nativeListModel(items);
  assert.equal(model.style.fontSize, 24);
  assert.equal(model.style.autoFit, 'none');
  assert.deepEqual(model.paragraphs.map(p => p.runs[0]), items);
  assert.deepEqual(model.paragraphs.map(p => p.spaceAfter), [1200, 1200, 0]);
  assert.equal(model.paragraphs[0].marginLeft, 228600);
  assert.equal(model.paragraphs[0].indent, -114300);
});
test('invalid lists fail instead of silently shrinking or losing structure', () => {
  for (const items of [[], [''], ['a\n\nb'], [3]]) assert.throws(() => nativeListModel(items));
  assert.throws(() => nativeListModel(['a'], {fontSizePt: 13.5}));
  assert.throws(() => nativeListModel(['a'], {gapPt: NaN}));
  assert.throws(() => nativeListModel(['a'], {marginLeftPt: 2, hangingPt: 9}));
  assert.equal(nativeListModel(['a']).paragraphs[0].spaceAfter, 0);
});

test('explicit CJK line breaks stay inside the same native list item', () => {
  const model = nativeListModel(['第一行\n第二行', '下一项']);
  assert.equal(model.paragraphs.length, 2);
  assert.equal(model.paragraphs[0].runs[0], '第一行\n第二行');
});

test('rendered list gate catches isolated markers and actual point-size violations', () => {
  assert.deepEqual(checkNativeListLayout({resolvedFontSize:24,textLayout:{lines:[{text:'• 内容'}]}}),[]);
  assert.ok(checkNativeListLayout({resolvedFontSize:18,textLayout:{lines:[{text:'•'}]}}).includes('ORPHAN_LIST_MARKER'));
  assert.ok(checkNativeListLayout({resolvedFontSize:18,textLayout:{lines:[{text:'• 内容'}]}}).includes('FONT_BELOW_POINT_FLOOR'));
  assert.ok(checkNativeListLayout({resolvedFontSize:24}).includes('LIST_LINES_UNRESOLVED'));
});
