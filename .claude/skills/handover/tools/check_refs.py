#!/usr/bin/env python3
""".handover/ 산출물 사이의 참조 무결성을 검사한다.

검증 훅은 파일 하나만 보므로 "다른 파일을 가리키는 값"은 확인하지 못한다.
이 도구가 파이프라인 종료 후(또는 에이전트 하나가 끝날 때마다) 그 틈을 메운다.

사용법: python3 check_refs.py [프로젝트 루트=.] [--strict]
        종료 코드: 0 = 오류 없음, 1 = 오류 있음 (--strict면 경고도 오류로 취급)

검사하는 것 (파일이 없으면 해당 검사는 건너뛰고 "건너뜀"으로 표시한다):
- repo_map.stacks[].id를 가리키는 모든 stack_id
  (env_report, api_contracts의 clients/scope/caller/callee/candidates)
- env_report.dependencies_resolved[].name이 repo_map.external_deps에 있는지 (경고:
  env-builder가 새로 발견했을 수 있다)
- 인덱스(features.json 등)의 detail_path 파일 존재, 상세 파일 ID가 인덱스에 있는지
- features.json의 contract_ids가 api_contracts에 있는지, 어떤 기능에도 안 들어간 계약이
  있는지 (경고: features.json의 unknowns에 그 계약 ID가 있으면 사유가 남은 것으로 보고 통과)
- 상세 파일의 related(claim.related)가 가리키는 항목 ID가 인덱스/api_contracts에 있는지
- history.json의 hotspots/ownership/notable_commits 경로가 레포에 실제로 있는지 (경고:
  삭제·이름 변경된 경로일 수 있다)
- 산출물 meta.commit이 서로 같은지 (경고: 오래된 산출물이 섞였다는 신호)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

INDEX_FILES = ("features.json", "tech_elements.json", "external_deps.json")
DETAIL_DIRS = ("traces", "tech", "ext")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.skipped: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def skip(self, msg: str) -> None:
        self.skipped.append(msg)


def load(path: Path, r: Report):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        r.error(f"{path.name}: JSON 파싱 실패 ({e})")
        return None


def check_stack_refs(doc: dict | None, name: str, stack_ids: set[str], r: Report, repo_map_present: bool) -> None:
    if doc is None:
        return
    if not repo_map_present:
        r.skip(f"{name}의 stack_id 참조 (repo_map.json 없음)")
        return

    def chk(sid, where):
        if sid is not None and sid not in stack_ids:
            r.error(f"{name}: {where}의 stack_id '{sid}'가 repo_map.stacks에 없음")

    if name == "env_report.json":
        for i, c in enumerate(doc.get("components", [])):
            chk(c.get("stack_id"), f"components[{i}]")
    elif name == "api_contracts.json":
        for i, d in enumerate(doc.get("meta", {}).get("scope", [])):
            chk(d.get("caller_stack_id"), f"meta.scope[{i}].caller_stack_id")
            chk(d.get("callee_stack_id"), f"meta.scope[{i}].callee_stack_id")
        for i, c in enumerate(doc.get("clients", [])):
            chk(c.get("stack_id"), f"clients[{i}]")
        for i, c in enumerate(doc.get("contracts", [])):
            label = f"contracts[{i}]({c.get('id')})"
            for side in ("caller", "callee"):
                if c.get(side):
                    chk(c[side].get("stack_id"), f"{label}.{side}")
            for k, cand in enumerate(c.get("candidates") or []):
                chk(cand.get("stack_id"), f"{label}.candidates[{k}]")


def check_env_vs_repo_map(env: dict | None, repo_map: dict | None, r: Report) -> None:
    if env is None or repo_map is None:
        return
    known = {d.get("name") for d in repo_map.get("external_deps", [])}
    for i, d in enumerate(env.get("dependencies_resolved", [])):
        if d.get("name") not in known:
            r.warn(
                f"env_report.json: dependencies_resolved[{i}] '{d.get('name')}'은 "
                "repo_map.external_deps에 없음 (env-builder가 새로 발견한 것이면 정상)"
            )


def check_items(handover: Path, contracts: dict | None, r: Report) -> None:
    indexes = {n: load(handover / n, r) for n in INDEX_FILES}
    indexes = {n: d for n, d in indexes.items() if d is not None}
    details = {}
    for d in DETAIL_DIRS:
        for p in sorted((handover / d).glob("*.json")) if (handover / d).is_dir() else []:
            doc = load(p, r)
            if doc is not None:
                details[p] = doc
    if not indexes and not details:
        r.skip("인덱스/상세 파일 참조 (인덱스·상세 파일 없음)")
        return

    index_ids: set[str] = set()
    detail_paths_in_index: set[str] = set()
    for name, doc in indexes.items():
        for i, item in enumerate(doc.get("items", [])):
            index_ids.add(item.get("id"))
            dp = item.get("detail_path")
            if dp:
                detail_paths_in_index.add(dp)
                if item.get("status") == "done" and not (handover / dp).is_file():
                    r.error(f"{name}: items[{i}]({item.get('id')})의 detail_path 파일이 없음 ({dp})")

    contract_ids = {c.get("id") for c in (contracts or {}).get("contracts", [])}
    known_ids = index_ids | contract_ids

    features = indexes.get("features.json")
    if features is not None:
        if contracts is None:
            r.skip("features.json의 contract_ids 참조 (api_contracts.json 없음)")
        else:
            assigned: set[str] = set()
            for i, item in enumerate(features.get("items", [])):
                for cid in item.get("contract_ids", []):
                    assigned.add(cid)
                    if cid not in contract_ids:
                        r.error(f"features.json: items[{i}]({item.get('id')})의 contract_ids '{cid}'가 api_contracts에 없음")
            explained = {u.get("topic") for u in features.get("unknowns", [])}
            for cid in sorted(contract_ids - assigned - explained):
                r.warn(f"api_contracts.json: 계약 {cid}가 어떤 기능에도 속하지 않고 features.json unknowns에도 사유가 없음")

    for p, doc in details.items():
        rel = p.relative_to(handover).as_posix()
        if rel not in detail_paths_in_index:
            r.warn(f"{rel}: 어떤 인덱스의 detail_path에도 등록되지 않은 상세 파일")
        if doc.get("id") not in index_ids:
            r.error(f"{rel}: id '{doc.get('id')}'가 어떤 인덱스에도 없음")
        for i, c in enumerate(doc.get("claims", [])):
            for ref in c.get("related", []):
                if ref not in known_ids:
                    r.error(f"{rel}: claims[{i}].related '{ref}'가 인덱스/api_contracts에 없음")


def check_history_paths(root: Path, history: dict | None, r: Report) -> None:
    if history is None:
        return
    targets: list[tuple[str, str]] = []
    for key in ("hotspots", "ownership"):
        targets += [(f"{key}[{i}]", x.get("path", "")) for i, x in enumerate(history.get(key, []))]
    for i, c in enumerate(history.get("notable_commits", [])):
        targets += [(f"notable_commits[{i}]", p) for p in c.get("affected_paths", [])]
    for where, path in targets:
        if path and not (root / path).exists():
            r.warn(f"history.json: {where}의 경로가 현재 작업 트리에 없음 ({path}) — 삭제·이름 변경된 경로일 수 있음")


def check_commits(docs: dict[str, dict | None], r: Report) -> None:
    commits: dict[str, str] = {}
    for name, doc in docs.items():
        if doc is None:
            continue
        sha = (doc.get("meta") or {}).get("commit") or doc.get("commit")
        if sha:
            commits[name] = sha
    if len(set(commits.values())) > 1:
        detail = ", ".join(f"{n}={c[:7]}" for n, c in sorted(commits.items()))
        r.warn(f"산출물마다 분석한 commit이 다름 ({detail}) — 오래된 산출물이 섞여 있을 수 있음")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    root = Path(args[0] if args else ".").resolve()
    handover = root / ".handover"
    if not handover.is_dir():
        print(f"{handover} 가 없음", file=sys.stderr)
        return 1

    r = Report()
    repo_map = load(handover / "repo_map.json", r)
    history = load(handover / "history.json", r)
    env = load(handover / "env_report.json", r)
    contracts = load(handover / "api_contracts.json", r)
    stack_ids = {s.get("id") for s in (repo_map or {}).get("stacks", [])}

    if repo_map is None:
        r.skip("repo_map.json 없음 — stack_id 참조 검사 생략")
    check_stack_refs(env, "env_report.json", stack_ids, r, repo_map is not None)
    check_stack_refs(contracts, "api_contracts.json", stack_ids, r, repo_map is not None)
    check_env_vs_repo_map(env, repo_map, r)
    check_items(handover, contracts, r)
    check_history_paths(root, history, r)
    check_commits({"repo_map": repo_map, "history": history, "env_report": env, "api_contracts": contracts}, r)

    for e in r.errors:
        print(f"[오류] {e}")
    for w in r.warnings:
        print(f"[경고] {w}")
    for s in r.skipped:
        print(f"[건너뜀] {s}")
    print(f"\n오류 {len(r.errors)}건, 경고 {len(r.warnings)}건")
    return 1 if r.errors or (strict and r.warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
