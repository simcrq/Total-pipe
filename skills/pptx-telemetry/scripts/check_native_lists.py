"""Opt-in OOXML list contract checks; not a substitute for rendered visual QA.

Usage: python check_native_lists.py deck.pptx --slide 11 --shape scope-evidence-body
"""
import argparse
import json
import xml.etree.ElementTree as ET
import zipfile

NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}


def check_list(shape, minimum_pt=17, expected_items=3):
    issues = []
    body = shape.find('p:txBody', NS)
    if body is None:
        return ['MISSING_TEXT_BODY']
    paragraphs = body.findall('a:p', NS)
    if len(paragraphs) != expected_items:
        issues.append('LIST_ITEM_COUNT')
    auto = body.find('a:bodyPr/a:normAutofit', NS)
    scale = float(auto.get('fontScale', '100000')) / 100000 if auto is not None else 1
    if auto is not None:
        issues.append('LIST_SHRINK_ENABLED')
    gaps = []
    for i, p in enumerate(paragraphs):
        prop = p.find('a:pPr', NS)
        marker = p.find('a:pPr/a:buChar', NS)
        if marker is None:
            issues.append('NATIVE_BULLET_MISSING')
        if prop is None or not (int(prop.get('marL', '0')) > 0 and int(prop.get('indent', '0')) < 0
                               and int(prop.get('marL', '0')) + int(prop.get('indent', '0')) >= 0):
            issues.append('BULLET_HANGING_INDENT_INVALID')
        default = p.find('a:pPr/a:defRPr', NS)
        for run in p.findall('a:r', NS):
            if not ''.join(run.itertext()).strip():
                continue
            run_prop = run.find('a:rPr', NS)
            size = run_prop.get('sz') if run_prop is not None else None
            if size is None and default is not None:
                size = default.get('sz')
            if size is None:
                issues.append('FONT_SIZE_UNRESOLVED')
            elif float(size) / 100 * scale < minimum_pt:
                issues.append('FONT_BELOW_POINT_FLOOR')
        if i < len(paragraphs) - 1:
            gap = p.find('a:pPr/a:spcAft/a:spcPts', NS)
            gaps.append(int(gap.get('val', '0')) if gap is not None else 0)
    if gaps and (min(gaps) <= 0 or len(set(gaps)) != 1):
        issues.append('LIST_GAPS_NOT_EXPLICIT_AND_EQUAL')
    return sorted(set(issues))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pptx')
    parser.add_argument('--slide', type=int, required=True)
    parser.add_argument('--shape', action='append', required=True)
    parser.add_argument('--minimum-pt', type=float, default=17)
    parser.add_argument('--items', type=int, default=3)
    args = parser.parse_args()
    with zipfile.ZipFile(args.pptx) as archive:
        root = ET.fromstring(archive.read(f'ppt/slides/slide{args.slide}.xml'))
    results = {}
    for name in args.shape:
        shapes = [s for s in root.findall('.//p:sp', NS)
                  if s.find('p:nvSpPr/p:cNvPr', NS).get('name') == name]
        results[name] = check_list(shapes[0], args.minimum_pt, args.items) if len(shapes) == 1 else ['SHAPE_NOT_UNIQUE']
    print(json.dumps({'scope': 'OOXML typography and paragraph spacing, not rendered line geometry',
                      'shapes': results, 'pass': not any(results.values())}, ensure_ascii=False, indent=2))
    return int(any(results.values()))


if __name__ == '__main__':
    raise SystemExit(main())
