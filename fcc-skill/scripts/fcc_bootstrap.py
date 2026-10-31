#!/usr/bin/env python3
"""Standalone AI Bootstrap entry: Python stdlib only, paired with FCC Core."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    core_script = Path(__file__).resolve().parents[2] / 'fcc-core/src/fcc/bootstrap.py'
    if core_script.is_file():
        return runpy.run_path(str(core_script))['main'](argv)
    if not argv or argv[0] != 'doctor':
        print('FCC Core is not bundled. This SKILL can only conduct a conversational intake without the Core.', file=sys.stderr)
        return 2
    result = {
        'version': '1.5.0', 'mode': 'conversation_only',
        'core': {'source_present': False},
        'ai_host': {'can_execute_commands': 'unknown', 'persistent_runtime': 'unknown'},
        'service': {'reachable_fcc': False, 'scheduler_running': False, 'report_delivery': 'not_configured'},
        'next_action': '请先访谈目标、测量方法、数据来源、反馈节拍和执行边界；如果用户需要持续运行，再提供完整 Core 部署说明。',
        'source_credit': 'FCC Control Studio — Chans (chenchen_wang@foxmail.com)'}
    if '--json' in argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result['next_action'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
