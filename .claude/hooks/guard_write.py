#!/usr/bin/env python3
"""PreToolUse hook: 파일 쓰기 도구가 <프로젝트>/.handover/ 밖을 건드리지 못하게 막는다.

인수인계 분석 중에는 코드를 읽기만 해야 하므로, 대상 프로젝트의
.claude/settings.local.json 에 이 훅을 걸어두면 메인 세션과 서브에이전트 모두
.handover/ 아래에만 쓸 수 있다.

주의: Bash를 통한 쓰기(echo > file, sed -i 등)는 이 훅으로 막히지 않는다.
Bash 권한은 permissions 설정으로 따로 제한해야 한다.
"""
import json
import sys
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") not in WRITE_TOOLS:
        return 0

    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return 0

    cwd = Path(payload.get("cwd", ".")).resolve()
    allowed = (cwd / ".handover").resolve()
    p = Path(raw)
    target = (p if p.is_absolute() else cwd / p).resolve()

    if target == allowed or allowed in target.parents:
        return 0

    print(
        f"쓰기 차단: 인수인계 분석 중에는 .handover/ 아래에만 쓸 수 있습니다. (요청 경로: {raw})\n"
        "코드 수정이 필요해 보이면 findings.json에 개선 제안으로 기록하세요.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
