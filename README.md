# Handover harness — schemas & hooks (v0.1)

## 설치
1. `.claude/` 아래 내용을 `~/.claude/`에 복사
2. `pip install jsonschema`  (referencing 포함)
3. 분석할 프로젝트에서:
   - `project-settings/settings.local.json` → `<프로젝트>/.claude/settings.local.json`
   - `.handover/`, `.claude/settings.local.json`을 `.gitignore`에 추가

## 구성
- `skills/handover/schemas/common.schema.json` — ID, 근거(evidence), 주장(claim) 공통 정의
- `skills/handover/schemas/index.schema.json` — features.json / tech_elements.json / external_deps.json
- `skills/handover/schemas/detail.schema.json` — traces/*, tech/*, ext/*
- `skills/handover/examples/` — 에이전트 프롬프트에 예시로 넣을 정상 산출물
- `hooks/guard_write.py` — PreToolUse: .handover/ 밖 쓰기 차단
- `hooks/validate_artifact.py` — PostToolUse: 스키마 + 의미 규칙 검증, 실패 시 exit 2

## 수동 검증
python3 ~/.claude/hooks/validate_artifact.py --file <프로젝트>/.handover/features.json
