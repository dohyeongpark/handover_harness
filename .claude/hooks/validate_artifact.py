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
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "skills" / "handover" / "schemas"
MAX_ERRORS = 20
LINE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")

INDEX_FILES = {"features.json", "tech_elements.json", "external_deps.json"}
# 인덱스 파일마다 kind, 허용되는 항목 type, 상세 파일 디렉터리가 정해져 있다
INDEX_SPEC = {
    "features.json": ("feature_index", ("service_feature", "flow"), "traces"),
    "tech_elements.json": ("tech_index", ("tech_element",), "tech"),
    "external_deps.json": ("external_index", ("external_dependency",), "ext"),
}
TYPE_PREFIX = {"service_feature": "FEAT-", "flow": "FLOW-", "tech_element": "TECH-", "external_dependency": "EXT-"}
DETAIL_DIRS = {"traces", "tech", "ext"}
DIR_PREFIX = {"traces": ("FEAT-", "FLOW-"), "tech": ("TECH-",), "ext": ("EXT-",)}
REPO_MAP_FILE = "repo_map.json"
HISTORY_FILE = "history.json"
ENV_REPORT_FILE = "env_report.json"
API_CONTRACTS_FILE = "api_contracts.json"
MATCH_STATUSES = ("matched", "caller_only", "callee_only", "ambiguous")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_SUFFIX_RE = re.compile(r"^(.*?)(?:-(\d+))?$")
ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


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
        "history": Draft202012Validator(schemas["history.schema.json"], registry=registry),
        "env_report": Draft202012Validator(schemas["env_report.schema.json"], registry=registry),
        "api_contracts": Draft202012Validator(schemas["api_contracts.schema.json"], registry=registry),
    }


# ---------------------------------------------------------------- path helpers
def locate(file_path: Path) -> tuple[Path, Path, str | None]:
    """(repo_root, handover_dir, kind) 반환. kind: 'index' | 'detail' | 'repo_map' | 'history' | 'env_report' | 'api_contracts' | None"""
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
    if len(rel.parts) == 1 and rel.name == HISTORY_FILE:
        return repo_root, handover, "history"
    if len(rel.parts) == 1 and rel.name == ENV_REPORT_FILE:
        return repo_root, handover, "env_report"
    if len(rel.parts) == 1 and rel.name == API_CONTRACTS_FILE:
        return repo_root, handover, "api_contracts"
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


def semantic_index(doc: dict, file_path: Path, handover: Path, errors: list[str]) -> None:
    kind, allowed_types, detail_dir = INDEX_SPEC[file_path.name]
    if doc.get("kind") != kind:
        errors.append(f"{file_path.name}의 kind는 '{kind}'여야 함 ({doc.get('kind')})")
    seen = set()
    for i, item in enumerate(doc.get("items", [])):
        iid = item.get("id")
        if iid in seen:
            errors.append(f"items[{i}]: 중복 ID {iid}")
        seen.add(iid)
        itype = item.get("type")
        if itype not in allowed_types:
            errors.append(f"items[{i}]: {file_path.name}에는 {'/'.join(allowed_types)} 항목만 둘 수 있음 ({itype})")
        elif not (iid or "").startswith(TYPE_PREFIX[itype]):
            errors.append(f"items[{i}]: type이 {itype}이면 ID는 {TYPE_PREFIX[itype]}로 시작해야 함 ({iid})")
        if not item.get("detail_path", "").startswith(detail_dir + "/"):
            errors.append(f"items[{i}]: {file_path.name}의 detail_path는 {detail_dir}/ 아래여야 함 ({item.get('detail_path')})")
        if (item.get("trigger") or item.get("contract_ids")) and itype != "service_feature":
            errors.append(f"items[{i}]: trigger와 contract_ids는 service_feature에만 쓴다 ({iid})")
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
    dep_keys = [(d.get("name"), d.get("profile")) for d in doc.get("external_deps", [])]
    dup_deps = {x for x in dep_keys if dep_keys.count(x) > 1}
    if dup_deps:
        errors.append(f"중복 external_deps (name, profile): {sorted(dup_deps)}")

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


def _parse_datetime(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def semantic_history(doc: dict, errors: list[str]) -> None:
    for i, c in enumerate(doc.get("notable_commits", [])):
        sha = c.get("sha", "")
        if not SHA_RE.match(sha):
            errors.append(f"notable_commits[{i}]: sha는 40자 hex여야 함 ({sha})")
        if c.get("kind") == "other" and not (c.get("kind_note") or "").strip():
            errors.append(
                f"notable_commits[{i}]: kind가 other면 kind_note에 기존 분류에 맞지 않는 이유를 적어야 함"
            )

    for key in ("hotspots", "ownership"):
        paths = [item.get("path") for item in doc.get(key, [])]
        dup = {x for x in paths if paths.count(x) > 1}
        if dup:
            errors.append(f"중복 {key} path: {sorted(dup)}")

    activity = doc.get("repo_activity", {})
    first = _parse_datetime(activity.get("first_commit_date", ""))
    last = _parse_datetime(activity.get("last_commit_date", ""))
    if first is not None and last is not None and first > last:
        errors.append(
            f"repo_activity: first_commit_date({activity['first_commit_date']})가 "
            f"last_commit_date({activity['last_commit_date']})보다 나중임"
        )


def semantic_env_report(doc: dict, errors: list[str]) -> None:
    stack_ids = [c.get("stack_id") for c in doc.get("components", [])]
    dup_stacks = {x for x in stack_ids if stack_ids.count(x) > 1}
    if dup_stacks:
        errors.append(f"중복 components stack_id: {sorted(dup_stacks)}")

    for i, c in enumerate(doc.get("components", [])):
        if c.get("final_state") != "running_verified":
            continue
        started = any(
            s.get("phase") == "start" and s.get("outcome") == "success" for s in c.get("steps", [])
        )
        if not started:
            errors.append(
                f"components[{i}]: final_state가 running_verified인데 steps에 "
                f"start phase의 success가 없음 ({c.get('stack_id')})"
            )

    dep_keys = [(d.get("name"), d.get("profile")) for d in doc.get("dependencies_resolved", [])]
    dup_deps = {x for x in dep_keys if dep_keys.count(x) > 1}
    if dup_deps:
        errors.append(f"중복 dependencies_resolved (name, profile): {sorted(dup_deps)}")

    for i, p in enumerate(doc.get("processes", [])):
        if p.get("started") and not p.get("cleaned_up") and not (p.get("cleanup_note") or "").strip():
            errors.append(
                f"processes[{i}]: started인데 cleaned_up이 false임 ({p.get('name')}). "
                "프로세스를 종료하고 cleaned_up을 true로 바꾸거나, 정말 정리하지 못했다면 "
                "그 이유와 수동 정리 방법을 cleanup_note에 남겨야 함"
            )

    budget = doc.get("meta", {}).get("budget", {})
    used, limit = budget.get("attempts_used"), budget.get("max_attempts")
    if isinstance(used, int) and isinstance(limit, int) and used > limit:
        errors.append(f"meta.budget: attempts_used({used})가 max_attempts({limit})를 넘음")


def expected_contract_id_base(method: str, path_normalized: str) -> str:
    """API-<METHOD>-<경로 세그먼트를 '-'로 이은 것, {}는 by>. 루트 경로면 API-<METHOD>."""
    segs = [("by" if s == "{}" else ID_UNSAFE_RE.sub("-", s).strip("-")) for s in path_normalized.split("/") if s]
    segs = [s for s in segs if s]
    return "-".join(["API", method, *segs])


def check_contract_id(c: dict, where: str, ids: set, errors: list[str]) -> None:
    cid = c.get("id") or ""
    base = expected_contract_id_base(c.get("method", ""), c.get("path_normalized", ""))
    if cid == base:
        return
    m = ID_SUFFIX_RE.match(cid)
    stem, num = (m.group(1), m.group(2)) if m else (cid, None)
    if stem != base or num is None:
        errors.append(f"{where}: id는 method와 path_normalized에서 유도한 '{base}'(충돌 시 -2, -3 접미사)여야 함 ({cid})")
        return
    # 접미사 ID는 충돌 상대가 실제로 있어야 하고, 이유를 note에 남겨야 한다
    prior = base if int(num) == 2 else f"{base}-{int(num) - 1}"
    if int(num) < 2 or prior not in ids:
        errors.append(f"{where}: 접미사 -{num}는 앞선 ID '{prior}'가 있을 때만, 2부터 순서대로 붙일 수 있음 ({cid})")
    note_owner = c.get("caller") or c.get("callee") or {}
    if not (note_owner.get("note") or "").strip():
        errors.append(f"{where}: 접미사 ID는 충돌한 ID와 차이를 caller.note(없으면 callee.note)에 남겨야 함")


def semantic_api_contracts(doc: dict, errors: list[str]) -> None:
    client_ids = [c.get("id") for c in doc.get("clients", [])]
    dup_clients = {x for x in client_ids if client_ids.count(x) > 1}
    if dup_clients:
        errors.append(f"중복 client id: {sorted(dup_clients)}")
    known_clients = set(client_ids)

    contracts = doc.get("contracts", [])
    ids = [c.get("id") for c in contracts]
    dup_ids = {x for x in ids if ids.count(x) > 1}
    if dup_ids:
        errors.append(f"중복 contract id: {sorted(dup_ids)}")

    seen_ids: set = set()
    for i, c in enumerate(contracts):
        where = f"contracts[{i}]({c.get('id')})"
        check_contract_id(c, where, seen_ids, errors)
        seen_ids.add(c.get("id"))
        caller, callee = c.get("caller"), c.get("callee")
        status = c.get("match_status")
        candidates = c.get("candidates") or []

        if caller is not None and caller.get("client_id") not in known_clients:
            errors.append(f"{where}: caller.client_id가 clients에 없음 ({caller.get('client_id')})")

        if status == "matched":
            if caller is None or callee is None:
                errors.append(f"{where}: match_status가 matched인데 caller 또는 callee가 null임")
            if c.get("match_basis") is None:
                errors.append(f"{where}: match_status가 matched면 match_basis가 있어야 함")
        elif status == "caller_only":
            if caller is None:
                errors.append(f"{where}: match_status가 caller_only인데 caller가 null임")
            if callee is not None:
                errors.append(
                    f"{where}: match_status가 caller_only인데 callee가 null이 아님. "
                    "짝을 찾았으면 matched로, 확신이 없으면 ambiguous로 두고 candidates에 기록"
                )
        elif status == "callee_only":
            if callee is None:
                errors.append(f"{where}: match_status가 callee_only인데 callee가 null임")
            if caller is not None:
                errors.append(
                    f"{where}: match_status가 callee_only인데 caller가 null이 아님. "
                    "짝을 찾았으면 matched로, 확신이 없으면 ambiguous로 두고 candidates에 기록"
                )
        elif status == "ambiguous":
            if (caller is None) == (callee is None):
                errors.append(
                    f"{where}: ambiguous면 확정된 쪽만 채우고 확정하지 못한 쪽은 null이어야 함 "
                    "(caller/callee 중 정확히 하나만 null)"
                )
            else:
                open_side = "caller" if caller is None else "callee"
                if len(candidates) < 2:
                    errors.append(f"{where}: ambiguous면 candidates에 후보를 2개 이상 기록해야 함")
                wrong = [k for k, cand in enumerate(candidates) if cand.get("side") != open_side]
                if wrong:
                    errors.append(
                        f"{where}: candidates{wrong}의 side가 확정하지 못한 쪽({open_side})과 다름"
                    )

        if status in ("caller_only", "callee_only") and c.get("match_basis") is not None:
            errors.append(f"{where}: 짝을 짓지 않은 {status}는 match_basis가 null이어야 함")
        if status != "ambiguous" and candidates:
            errors.append(f"{where}: candidates는 match_status가 ambiguous일 때만 쓴다")

    summary = doc.get("summary", {})
    if summary.get("total") != len(contracts):
        errors.append(f"summary.total({summary.get('total')})이 contracts 개수({len(contracts)})와 다름")
    by_status = summary.get("by_match_status", {})
    for status in MATCH_STATUSES:
        actual = sum(1 for c in contracts if c.get("match_status") == status)
        if by_status.get(status) != actual:
            errors.append(
                f"summary.by_match_status.{status}({by_status.get(status)})가 실제 개수({actual})와 다름"
            )


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
        semantic_index(doc, file_path, handover, errors)
    elif kind == "detail":
        semantic_detail(doc, file_path, errors)
    elif kind == "repo_map":
        semantic_repo_map(doc, errors)
    elif kind == "history":
        semantic_history(doc, errors)
    elif kind == "env_report":
        semantic_env_report(doc, errors)
    elif kind == "api_contracts":
        semantic_api_contracts(doc, errors)
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
