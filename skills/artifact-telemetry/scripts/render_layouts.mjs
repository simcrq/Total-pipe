// Generic artifact-tool layout renderer.
// Drives @oai/artifact-tool's Presentation API from a declarative deck spec
// and exports one openai.presentation.layout/v4 document per slide.
//
// Usage:
//   node render_layouts.mjs <deck-spec.json>
//
// Deck spec:
// {
//   "artifact_tool": "F:/Workbuddy/artifact-tool",   // repo root (has dist/artifact_tool.mjs)
//   "out_dir":       "<project>/telemetry/layouts",
//   "assets_dir":    "<project>/assets",
//   "font":          "Microsoft YaHei",              // optional
//   "colors":        {"deep": "#0E3F8C", ...},       // optional palette used by names below
//   "slides": [{
//     "name":   "01_cover",
//     "bar_h":  12,                                  // top bar height; 0 = no bar
//     "title":  "页面主标题",                          // null = no title bar
//     "footer": {"left": "组会汇报", "page": "01 / 06"},  // null = no footer
//     "elements": [
//       {"t":"rect",  "name":"panel",  "x":0,"y":100,"w":620,"h":560, "fill":"#F7F9FC","line":"#D6DCE5","geometry":"roundRect"},
//       {"t":"text",  "name":"title",  "x":40,"y":8,"w":1200,"h":72,"text":"标题","fs":34,"color":"#0E3F8C","bold":true,"align":"center","valign":"middle","wrap":"none"},
//       {"t":"pill",  "name":"badge",  "x":470,"y":540,"w":150,"h":32,"fill":"#1E4FA8","text":"标签","fs":18,"color":"#FFFFFF"},
//       {"t":"dot",   "name":"dot1",   "x":40,"y":430,"w":10,"h":10,"fill":"#1E4FA8"},
//       {"t":"image", "name":"fig1",   "key":"fig1-phonons","file":"fig1.jpg","desc":"图1 · ...",
//                     "x":90,"y":132,"w":512,"h":289},   // w/h = display frame; use the asset's aspect to avoid letterbox
//       {"t":"card",  "name":"c1",     "x":.., "y":.., "w":.., "h":.., "fill":"#E8EFF8",   // alias of rect
//         "children":[ {"t":"text", ...}, {"t":"pill", ...} ]   // children: x/y are relative to the card
//     ]
//   }]
// }
//
// Output: <out_dir>/<slide name>.json  (schema openai.presentation.layout/v4, unit px, frame 1280x720)
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const specPath = process.argv[2];
if (!specPath) { console.error("usage: node render_layouts.mjs <deck-spec.json>"); process.exit(2); }
const spec = JSON.parse(fs.readFileSync(specPath, "utf-8"));

const AT = spec.artifact_tool ?? "F:/Workbuddy/artifact-tool";
const T = await import(pathToFileURL(path.join(AT, "dist", "artifact_tool.mjs")).href);
fs.mkdirSync(spec.out_dir, { recursive: true });

const FONT = spec.font ?? "Microsoft YaHei";
const PAL = spec.colors ?? {};

function textShape(slide, o) {
  const s = slide.shapes.add({
    geometry: o.geometry ?? "rect",
    position: { left: o.x, top: o.y, width: o.w, height: o.h },
    ...(o.fill ? { fill: { type: "solid", color: o.fill } } : {}),
    ...(o.line ? { line: { style: "solid", fill: o.line, width: o.lineW ?? 1 } } : {}),
  });
  s.name = o.name;
  s.text.style = { fontSize: o.fs ?? 20, color: o.color ?? "#1A2230", typeface: o.typeface ?? FONT, ...(o.bold ? { bold: true } : {}), ...(o.align ? { alignment: o.align } : {}), ...(o.valign ? { verticalAlignment: o.valign } : {}), ...(o.wrap ? { wrap: o.wrap } : {}) };
  s.text = o.text;
  return s;
}

function rect(slide, o) {
  const s = slide.shapes.add({
    geometry: o.geometry ?? "rect",
    position: { left: o.x, top: o.y, width: o.w, height: o.h },
    ...(o.fill ? { fill: { type: "solid", color: o.fill } } : {}),
    ...(o.line ? { line: { style: "solid", fill: o.line, width: o.lineW ?? 1 } } : {}),
  });
  s.name = o.name;
  return s;
}

const KIND = {
  rect: (slide, o) => rect(slide, o),
  card: (slide, o) => rect(slide, o),
  text: (slide, o) => textShape(slide, o),
  pill: (slide, o) => textShape(slide, { ...o, geometry: "roundRect" }),
  dot: (slide, o) => rect(slide, { ...o, geometry: "ellipse" }),
  image: (slide, o) => {
    const img = slide.images.add({
      path: path.join(spec.assets_dir, o.file),
      alt: `rpa:${o.key} | ${o.desc ?? o.name}`,
      position: { left: o.x, top: o.y, width: o.w, height: o.h },
      fit: o.fit ?? "contain",
    });
    img.name = o.name;
    return img;
  },
};

function addElement(slide, el, dx = 0, dy = 0) {
  const e = { ...el, x: (el.x ?? 0) + dx, y: (el.y ?? 0) + dy };
  const fn = KIND[e.t];
  if (!fn) throw new Error(`unknown element type: ${e.t}`);
  const made = fn(slide, e);
  for (const child of el.children ?? []) addElement(slide, child, e.x, e.y);
  return made;
}

const pres = T.Presentation.create();

for (const s of spec.slides) {
  const slide = pres.slides.add({ width: 1280, height: 720 });
  slide.background.fill = { type: "solid", color: s.background ?? "#FFFFFF" };
  if (s.bar_h) rect(slide, { name: "global-top-bar", x: 0, y: 0, w: 1280, h: s.bar_h, fill: PAL.deep ?? "#0E3F8C" });
  if (s.title) {
    textShape(slide, { name: "page-title", x: 40, y: 8, w: 1200, h: 72, text: s.title, fs: 34, color: PAL.deep ?? "#0E3F8C", bold: true, valign: "middle", wrap: "none", typeface: FONT });
  }
  if (s.footer) {
    textShape(slide, { name: "footer-left", x: 40, y: 672, w: 760, h: 22, text: s.footer.left ?? "", fs: 14, color: PAL.faint ?? "#5F6B7A", wrap: "none", typeface: FONT });
    textShape(slide, { name: "footer-page", x: 1100, y: 672, w: 140, h: 22, text: s.footer.page ?? "", fs: 14, color: PAL.deep ?? "#0E3F8C", align: "right", wrap: "none", typeface: FONT });
  }
  for (const el of s.elements ?? []) addElement(slide, el);
  const doc = await (await slide.export({ format: "layout" })).text();
  const out = path.join(spec.out_dir, `${s.name}.json`);
  fs.writeFileSync(out, doc);
  console.log("layout written:", out);
}
