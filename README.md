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
- `skills/handover/schemas/repo_map.schema.json` — repo_map.json (repo-mapper 산출물)
- `skills/handover/playbooks/` — 스택별 판별 신호·엔트리포인트·모듈 경계 참조 문서. 형식은 `playbooks/README.md` 참고
- `skills/handover/examples/` — 에이전트 프롬프트에 예시로 넣을 정상 산출물
- `agents/repo-mapper.md` — Phase 1: 스택 판별, 엔트리포인트, 모듈 의존, 외부 의존성 후보 지도 작성
- `hooks/guard_write.py` — PreToolUse: .handover/ 밖 쓰기 차단
- `hooks/validate_artifact.py` — PostToolUse: 스키마 + 의미 규칙 검증, 실패 시 exit 2

## 수동 검증
python3 ~/.claude/hooks/validate_artifact.py --file <프로젝트>/.handover/features.json

## 알려진 한계
`validate_artifact.py`의 evidence 검사는 파일 존재 여부와 라인 범위가
파일 길이 안에 드는지만 확인한다. 그 라인이 evidence의 설명(예: `symbol`)과
실제로 일치하는지는 확인하지 않는다 — 즉 엉뚱한 라인(예: 코드 대신 주석)을
가리켜도 스키마/의미 검사는 통과한다. moki-campaign에서 repo-mapper를 실행해
확인한 실제 사례. 지금은 사람 또는 후속 검증 단계(verifier)가 잡아야 하는
영역으로 남겨둔다.
