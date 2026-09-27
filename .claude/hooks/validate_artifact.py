#!/usr/bin/env python3
"""PostToolUse hook: .handover/ 아래 JSON 산출물을 스키마 + 의미 규칙으로 검증한다.

- 검증 대상이 아니면 조용히 exit 0
- 통과하면 exit 0
- 실패하면 오류를 stderr로 출력하고 exit 2 (Claude Code가 에이전트에게 전달해 수정하게 함)

단독 실행: python3 validate_artifact.py --file <path>   (훅 입력 없이 파일 하나 검증)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "skills" / "handover" / "schemas"
MAX_ERRORS = 20
LINE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")

INDEX_FILES = {"features.json", "tech_elements.json", "external_deps.json"}
DETAIL_DIRS = {"traces", "tech", "ext"}
DIR_PREFIX = {"traces": ("FEAT-", "FLOW-"), "tech": ("TECH-",), "ext": ("EXT-",)}
REPO_MAP_FILE = "repo_map.json"


# ---------------------------------------------------------------- schema loading
def load_validators(schema_dir: Path) -> dict[str, Draft202012Validator]:
    schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in schema_dir.glob("*.schema.json")}
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )
    return {
        "index": Draft202012Validator(schemas["index.schema.json"], registry=registry),
        "detail": Draft202012Validator(schemas["detail.schema.json"], registry=registry),
        "repo_map": Draft202012Validator(schemas["repo_map.schema.json"], registry=registry),
    }


# ---------------------------------------------------------------- path helpers
def locate(file_path: Path) -> tuple[Path, Path, str | None]:
    """(repo_root, handover_dir, kind) 반환. kind: 'index' | 'detail' | 'repo_map' | None"""
    parts = file_path.parts
    if ".handover" not in parts:
        return file_path.parent, file_path.parent, None
    idx = len(parts) - 1 - parts[::-1].index(".handover")
    handover = Path(*parts[: idx + 1])
    repo_root = handover.parent
    rel = file_path.relative_to(handover)
    if file_path.suffix != ".json":
        return repo_root, handover, None
    if len(rel.parts) == 1 and rel.name == REPO_MAP_FILE:
        return repo_root, handover, "repo_map"
    if len(rel.parts) == 1 and rel.name in INDEX_FILES:
        return repo_root, handover, "index"
    if len(rel.parts) == 2 and rel.parts[0] in DETAIL_DIRS:
        return repo_root, handover, "detail"
    return repo_root, handover, None


# ---------------------------------------------------------------- semantic checks
def check_evidence(ev: dict, repo_root: Path, where: str, errors: list[str], cache: dict) -> None:
    rel = ev.get("file", "")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        errors.append(f"{where}: evidence.file은 레포 루트 기준 상대 경로여야 함 ({rel})")
        return
    target = repo_root / p
    if not target.is_file():
        errors.append(f"{where}: 존재하지 않는 파일 ({rel})")
        return
    m = LINE_RE.match(ev.get("lines", ""))
    if not m:
        return  # 형식 오류는 스키마 단계에서 이미 보고됨
    start, end = int(m.group(1)), int(m.group(2) or m.group(1))
    key = str(target)
    if key not in cache:
        try:
            with target.open("rb") as f:
                cache[key] = sum(1 for _ in f)
        except OSError:
            cache[key] = None
    n = cache[key]
    if start < 1 or start > end:
        errors.append(f"{where}: 잘못된 라인 범위 {ev['lines']} ({rel})")
    elif n is not None and end > n:
        errors.append(f"{where}: 라인 범위 {ev['lines']}가 파일 길이 {n}줄을 넘음 ({rel})")


def walk_evidence(node, path: str):
    """문서 전체에서 evidence 객체를 (위치, 객체)로 순회"""
    if isinstance(node, dict):
        if "file" in node and "lines" in node:
            yield path, node
        for k, v in node.items():
            yield from walk_evidence(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_evidence(v, f"{path}[{i}]")


def semantic_index(doc: dict, handover: Path, errors: list[str]) -> None:
    seen = set()
    for i, item in enumerate(doc.get("items", [])):
        iid = item.get("id")
        if iid in seen:
            errors.append(f"items[{i}]: 중복 ID {iid}")
        seen.add(iid)
        dp = item.get("detail_path", "")
        if dp and Path(dp).stem != iid:
            errors.append(f"items[{i}]: detail_path 파일 이름이 ID와 다름 ({dp} vs {iid})")
        if item.get("status") == "done" and dp and not (handover / dp).is_file():
            errors.append(f"items[{i}]: status가 done인데 상세 파일이 없음 ({dp})")
        if item.get("status") == "failed" and not item.get("status_note"):
            errors.append(f"items[{i}]: status가 failed면 status_note로 이유를 남겨야 함")


def semantic_detail(doc: dict, file_path: Path, errors: list[str]) -> None:
    did = doc.get("id", "")
    if file_path.stem != did:
        errors.append(f"파일 이름({file_path.name})이 id({did})와 다름")
    prefixes = DIR_PREFIX.get(file_path.parent.name, ())
    if prefixes and not did.startswith(prefixes):
        errors.append(f"{file_path.parent.name}/ 디렉터리에는 {'/'.join(prefixes)} ID만 둘 수 있음 ({did})")

    claim_ids = []
    for i, c in enumerate(doc.get("claims", [])):
        cid = c.get("id", "")
        if cid.split("#")[0] != did:
            errors.append(f"claims[{i}]: claim ID는 '{did}#NN' 형식이어야 함 ({cid})")
        claim_ids.append(cid)
    dup = {x for x in claim_ids if claim_ids.count(x) > 1}
    if dup:
        errors.append(f"중복 claim ID: {sorted(dup)}")

    known = set(claim_ids)
    for i, s in enumerate(doc.get("steps", [])):
        for ref in s.get("claim_refs", []):
            if ref not in known:
                errors.append(f"steps[{i}]: 존재하지 않는 claim 참조 {ref}")
    for i, q in enumerate(doc.get("open_questions", [])):
        for ref in q.get("related_claims", []):
            if ref not in known:
                errors.append(f"open_questions[{i}]: 존재하지 않는 claim 참조 {ref}")

    orders = [s.get("order") for s in doc.get("steps", [])]
    if orders and sorted(orders) != list(range(1, len(orders) + 1)):
        errors.append(f"steps.order는 1부터 빠짐없이 이어져야 함 (현재 {orders})")


def semantic_repo_map(doc: dict, errors: list[str]) -> None:
    stack_ids = [s.get("id") for s in doc.get("stacks", [])]
    dup_stacks = {x for x in stack_ids if stack_ids.count(x) > 1}
    if dup_stacks:
        errors.append(f"중복 stack id: {sorted(dup_stacks)}")
    known_stacks = set(stack_ids)

    for i, e in enumerate(doc.get("entrypoints", [])):
        sid = e.get("stack_id")
        if sid not in known_stacks:
            errors.append(f"entrypoints[{i}]: 존재하지 않는 stack_id ({sid})")

    module_paths = [m.get("path") for m in doc.get("modules", [])]
    dup_modules = {x for x in module_paths if module_paths.count(x) > 1}
    if dup_modules:
        errors.append(f"중복 module path: {sorted(dup_modules)}")
    known_modules = set(module_paths)

    for i, m in enumerate(doc.get("modules", [])):
        sid = m.get("stack_id")
        if sid not in known_stacks:
            errors.append(f"modules[{i}]: 존재하지 않는 stack_id ({sid})")
        for dep in m.get("depends_on", []):
            if dep not in known_modules:
                errors.append(f"modules[{i}]: 존재하지 않는 depends_on 경로 ({dep})")


# ---------------------------------------------------------------- main
def validate_file(file_path: Path, validators) -> list[str]:
    repo_root, handover, kind = locate(file_path)
    if kind is None:
        return []
    try:
        doc = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"JSON 파싱 실패: {e}"]

    errors: list[str] = []
    for err in sorted(validators[kind].iter_errors(doc), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"[schema] {loc}: {err.message}")
    if errors:  # 구조가 깨진 상태에서 의미 검사는 노이즈만 만든다
        return errors

    cache: dict = {}
    for where, ev in walk_evidence(doc, "$"):
        check_evidence(ev, repo_root, where, errors, cache)
    if kind == "index":
        semantic_index(doc, handover, errors)
    elif kind == "detail":
        semantic_detail(doc, file_path, errors)
    elif kind == "repo_map":
        semantic_repo_map(doc, errors)
    return errors


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--file":
        file_path = Path(sys.argv[2]).resolve()
    else:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError:
            return 0
        raw = (payload.get("tool_input") or {}).get("file_path")
        if not raw:
            return 0
        p = Path(raw)
        file_path = (p if p.is_absolute() else Path(payload.get("cwd", ".")) / p).resolve()

    if not file_path.is_file():
        return 0
    validators = load_validators(SCHEMA_DIR)
    errors = validate_file(file_path, validators)
    if not errors:
        return 0

    shown = errors[:MAX_ERRORS]
    more = len(errors) - len(shown)
    print(f"산출물 검증 실패: {file_path.name} ({len(errors)}건)", file=sys.stderr)
    for e in shown:
        print(f"  - {e}", file=sys.stderr)
    if more:
        print(f"  ... 외 {more}건", file=sys.stderr)
    print("스키마와 규칙에 맞게 파일을 수정한 뒤 다시 저장하세요.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
