#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trigger_workflow.py — 手动触发 GitHub Actions 工作流（测试外部定时器用）
========================================================================
用法:
  python trigger_workflow.py --repo 用户名/仓库名 --token <GitHub_PAT>
  python trigger_workflow.py --repo user/repo --token xxx --workflow analyze.yml --ref main

成功会打印 HTTP 204（GitHub 的正常响应）。

Token 需要：Fine-grained PAT，仓库选中本项目，权限 Actions: Read and write。
详见 docs/外部定时器.md
"""

import json
import sys
import argparse
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser(description='手动触发 GitHub Actions 工作流')
    ap.add_argument('--repo', required=True, help='仓库，格式 owner/repo')
    ap.add_argument('--token', required=True, help='GitHub PAT（需 Actions: Read and write）')
    ap.add_argument('--workflow', default='analyze.yml', help='工作流文件名，默认 analyze.yml')
    ap.add_argument('--ref', default='main', help='分支名，默认 main')
    args = ap.parse_args()

    url = (f'https://api.github.com/repos/{args.repo}'
           f'/actions/workflows/{args.workflow}/dispatches')
    body = json.dumps({'ref': args.ref}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': f'Bearer {args.token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
        'User-Agent': 'trigger-workflow-script',
    })

    print(f'POST {url}\n     ref={args.ref}')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f'✅ HTTP {r.status} —— 触发成功')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:300]
        print(f'❌ HTTP {e.code} —— 触发失败\n   {detail}')
        hints = {
            401: 'token 无效或已过期',
            403: 'token 权限不足（需要 Actions: Read and write）',
            404: '仓库路径或分支名写错，或 token 没勾选这个仓库',
        }
        if e.code in hints:
            print(f'   可能原因：{hints[e.code]}')
        sys.exit(1)
    except Exception as e:
        print(f'❌ 请求失败：{e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
