import test from 'node:test';
import assert from 'node:assert/strict';
import { wrapCjk } from '../scripts/wrap_cjk.mjs';
const measureText = s => [...s].reduce((w,c)=>w+(/[\x00-\x7f]/u.test(c)?5:10),0);
const wrap = (text,widthPx=104) => wrapCjk(text,{widthPx,measureText});
const normalized = s=>s.replace(/\s/gu,'');

test('long CJK text fits width and preserves content',()=>{
  const text='作者报告补充实验扩展到其他材料并得到高保真图形';
  const out=wrap(text);
  assert.ok(out.includes('\n'));
  assert.equal(normalized(out),normalized(text));
  assert.ok(out.split('\n').every(s=>measureText(s)<=100));
});
test('chemical labels, English words, decimal number-unit pairs stay intact',()=>{
  const text='沉积在 SiO₂/Si 上的 2 nm MoS₂，接近 300 mW 时变化';
  const out=wrap(text,124);
  for(const token of ['SiO₂/Si','2 nm','MoS₂','300 mW']) assert.ok(out.includes(token),out);
  assert.equal(normalized(out),normalized(text));
});
test('opening punctuation cannot end a line; closing punctuation cannot start one',()=>{
  const out=wrap('实验（包含氧化、缺陷）显示：结果可靠。',74);
  for(const line of out.split('\n')) {
    assert.doesNotMatch(line,/^[，。！？；：、）]/u);
    assert.doesNotMatch(line,/[（]$/u);
  }
});
test('existing line breaks, combining marks and grapheme clusters survive',()=>{
  const out=wrap('结果e\u0301与👩‍🔬可靠\n下一行',104);
  assert.ok(out.includes('e\u0301')); assert.ok(out.includes('👩‍🔬'));
  assert.ok(out.endsWith('\n下一行'));
});
test('oversize unbreakable words and invalid metrics fail explicitly',()=>{
  assert.throws(()=>wrap('UnbreakableLongEnglishWord',24),RangeError);
  assert.throws(()=>wrapCjk('内容',{widthPx:100,measureText:()=>NaN}),TypeError);
  assert.throws(()=>wrap('内容',0),TypeError);
});
