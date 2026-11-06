#!/usr/bin/env python3
"""Read-only ListScene smoke using an installed Context Search script.

Run from a shell that already has the credentials exported. Never prints secrets,
resource IDs, raw API responses, or signs a write request.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, required=True,
                        help='Absolute path to the installed scripts/ctxsearch.mjs')
    parser.add_argument('--project', default='default')
    parser.add_argument('--scene-type', default='MULTIMODAL_SEARCH',
                        choices=['MULTIMODAL_SEARCH', 'AGENTIC_SEARCH', 'PSR'])
    args = parser.parse_args()
    node = shutil.which('node')
    if not node or not args.script.is_file():
        print(json.dumps({'passed': False, 'error': 'node or installed script not found'}))
        return 2
    flags = ['true', 'false', 'true'] if args.scene_type == 'MULTIMODAL_SEARCH' else ['true'] * 3
    command = [node, str(args.script.resolve()), '--action', 'ListScene', '--method', 'GET',
               '--param', f'Project={args.project}', '--param', f'SceneType={args.scene_type}',
               '--param', 'PageNumber=1', '--param', 'PageSize=10', '--timeout', '30000']
    for name, flag in zip(('IncludePsr', 'IncludeAgenticSearch', 'IncludeMultimodalSearch'), flags):
        command += ['--param', f'{name}={flag}']
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        print(json.dumps({'passed': False, 'error': 'smoke timeout'}))
        return 2
    report = {'test': 'ListScene', 'script': str(args.script.resolve()),
              'project': args.project, 'sceneType': args.scene_type,
              'pageNumber': 1, 'pageSize': 10, 'exitCode': completed.returncode}
    try:
        response = json.loads(completed.stdout if completed.returncode == 0 else completed.stderr)
    except json.JSONDecodeError:
        report.update(passed=False, error='non-JSON response or local error')
    else:
        metadata = response.get('ResponseMetadata', {})
        result = response.get('Result', {})
        result = result if isinstance(result, dict) else {}
        report.update(passed=completed.returncode == 0 and not metadata.get('Error')
                      and isinstance(result.get('Items'), list),
                      service=metadata.get('Service'), region=metadata.get('Region'),
                      itemsCount=len(result['Items']) if isinstance(result.get('Items'), list) else None,
                      total=result.get('Total'))
        if metadata.get('Error'):
            report['errorCode'] = metadata['Error'].get('Code')
        elif response.get('error'):
            # Errors generated locally by the transport are already redacted.
            report['error'] = response['error']
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
