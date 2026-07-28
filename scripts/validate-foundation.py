#!/usr/bin/env python3
from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parents[1]
required=['README.md','AI_CONTEXT.md','FOUNDATION_FROZEN.md','docs/FOUNDATION.md','docs/ROADMAP.md','docs/SYSTEM_DESIGN.md','docs/DATA_MODEL.md','docs/AI_RULES.md','docs/SOURCE_CATALOG.md','docs/GLOSSARY.md','docs/DECISIONS.md','docs/DEVELOPMENT_WORKFLOW.md','docs/PHASE1_ACCEPTANCE.md','docs/FREEZE_REVIEW.md','docs/SPEC_TEMPLATE.md','docs/DELIVERY_REPORT_TEMPLATE.md','docs/CHANGELOG.md','spec/SPEC_INDEX.md','spec/SPEC-0001.md','scripts/package-review.sh']
errors=[]
for f in required:
    if not (ROOT/f).is_file(): errors.append('missing: '+f)
link_re=re.compile(r'\[[^\]]+\]\(([^)]+)\)')
for md in ROOT.rglob('*.md'):
    for target in link_re.findall(md.read_text(encoding='utf-8')):
        if target.startswith(('http://','https://','#','mailto:')) or '://' in target: continue
        target=target.split('#',1)[0]
        if target and not (md.parent/target).resolve().exists(): errors.append(f'broken link: {md.relative_to(ROOT)} -> {target}')
markers={'README.md':'v2.1-FROZEN','docs/FOUNDATION.md':'状态：Frozen','FOUNDATION_FROZEN.md':'Result：PASS','spec/SPEC-0001.md':'Foundation：v2.1-FROZEN'}
for f,m in markers.items():
    if m not in (ROOT/f).read_text(encoding='utf-8'): errors.append(f'missing marker {m} in {f}')
if errors:
    print('FAIL'); [print('- '+e) for e in errors]; sys.exit(1)
print('PASS')
print('Required files:',len(required))
print('Markdown links: valid')
print('Freeze markers: valid')
