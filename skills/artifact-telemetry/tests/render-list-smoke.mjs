// Real-runtime integration test, explicitly invoked with an artifact-tool root.
// Uses a temporary directory; no installed dependencies or fixtures are changed.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

if (!process.argv[2]) throw new Error('Pass the installed artifact-tool package root');
const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'native-list-smoke-'));
const legacy = {t:'text', name:'legacy', x:20,y:20,w:500,h:60,text:'Unchanged text',fs:20};
const native = {t:'list',name:'native-list',x:20,y:120,w:500,h:240,
  items:['中文第一行\n中文第二行','另一个项目'],listStyle:{fontSizePt:18,gapPt:16}};
const automatic = {t:'list',name:'auto-list',x:20,y:400,w:340,h:240,
  items:['作者报告补充实验扩展到其他材料并得到高保真图形'],
  listStyle:{fontSizePt:18,gapPt:16,autoWrap:true}};
for (const [variant, elements] of [['old',[legacy]],['new',[legacy,native,automatic]]]) {
  const out = path.join(dir, variant);
  const specPath = path.join(dir, `${variant}.json`);
  await fs.writeFile(specPath, JSON.stringify({artifact_tool:process.argv[2],out_dir:out,assets_dir:dir,
    font:'Noto Sans CJK SC',slides:[{name:'test',elements}]}));
  execFileSync(process.execPath, [fileURLToPath(new URL('../scripts/render_layouts.mjs',import.meta.url)),specPath]);
}
const before = JSON.parse(await fs.readFile(path.join(dir,'old/test.json'),'utf8'));
const after = JSON.parse(await fs.readFile(path.join(dir,'new/test.json'),'utf8'));
const oldText = before.elements.find(e=>e.name==='legacy');
const newText = after.elements.find(e=>e.name==='legacy');
for (const field of ['text','bbox','resolvedFontSize','resolvedTextStyle','textLayout']) {
  assert.deepEqual(newText[field], oldText[field], `Legacy field changed: ${field}`);
}
const list = after.elements.find(e=>e.name==='native-list');
assert.equal(list.resolvedFontSize,24);
assert.equal(list.textLayout.lineCount,3);
assert.equal(list.paragraphs.length,2);
assert.deepEqual(list.textLayout.lines.map(line=>line.text),['• 中文第一行','中文第二行','• 另一个项目']);
const auto = after.elements.find(e=>e.name==='auto-list');
assert.ok(auto.textLayout.lineCount>1);
assert.equal(auto.paragraphs.length,1);
assert.equal(auto.text.replace(/\s/gu,''),automatic.items[0]);
assert.ok(auto.textLayout.lines.every(line=>line.text.trim()!=='•'));
console.log(`PASS: legacy rendering unchanged; native list is 24px/18pt with 2 bullets and 3 lines. Artifacts: ${dir}`);
