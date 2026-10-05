#!/usr/bin/env python3
""".handover/ 아래 JSON 산출물을 사람이 읽기 좋은 마크다운으로 바꾼다.

사용법: python3 render.py <json 경로> [--out <md 경로>]
        --out이 없으면 표준출력

산출물 종류는 파일명으로 판별한다 (검증 훅의 locate()와 같은 방식).
스키마에 맞지 않는 JSON이어도 죽지 않고, 읽을 수 있는 만큼 출력한 뒤
읽지 못한 부분을 맨 아래 "렌더링 중 발견한 문제"에 표시한다.

포맷터 추가: render_<kind>(doc, r) 함수를 만들고 FORMATTERS에 등록한다.
r(Report)은 마크다운 줄과 문제 목록을 모으는 객체다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

INDEX_FILES = {"features.json", "tech_elements.json", "external_deps.json"}
DETAIL_DIRS = {"traces", "tech", "ext"}
TOP_LEVEL_KINDS = {
    "repo_map.json": "repo_map",
    "history.json": "history",
    "env_report.json": "env_report",
    "api_contracts.json": "api_contracts",
}
MATCH_STATUSES = ("matched", "caller_only", "callee_only", "ambiguous")


# ---------------------------------------------------------------- kind 판별
def detect_kind(file_path: Path) -> str | None:
    parts = file_path.parts
    if ".handover" in parts:
        idx = len(parts) - 1 - parts[::-1].index(".handover")
        rel = Path(*parts[idx + 1 :])
    else:  # .handover 밖으로 복사해 둔 파일도 파일명으로 판별한다
        rel = Path(*parts[-2:]) if file_path.parent.name in DETAIL_DIRS else Path(file_path.name)
    if rel.suffix != ".json":
        return None
    if len(rel.parts) == 1 and rel.name in TOP_LEVEL_KINDS:
        return TOP_LEVEL_KINDS[rel.name]
    if len(rel.parts) == 1 and rel.name in INDEX_FILES:
        return "index"
    if len(rel.parts) == 2 and rel.parts[0] in DETAIL_DIRS:
        return "detail"
    return None


# ---------------------------------------------------------------- 출력 도우미
class Report:
    """마크다운 줄과, 렌더링 중 발견한 문제를 모은다."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.problems: list[str] = []

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)

    def problem(self, where: str, message: str) -> None:
        self.problems.append(f"`{where}`: {message}")

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        self.add("| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|")
        for row in rows:
            self.add("| " + " | ".join(cell(c) for c in row) + " |")
        self.add("")

    def text(self) -> str:
        out = list(self.lines)
        if self.problems:
            out += ["## ⚠ 렌더링 중 발견한 문제", ""]
            out += ["스키마와 다른 부분이다. 해당 값은 건너뛰거나 `?`로 표시했다.", ""]
            out += [f"- {p}" for p in self.problems]
            out.append("")
        return "\n".join(out).rstrip("\n") + "\n"


def cell(value) -> str:
    """표 칸에 넣을 수 있게 줄바꿈과 파이프를 처리"""
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def code(value) -> str:
    """인라인 코드. 값이 없으면 —, 백틱이 들어 있으면 이중 백틱으로 감싼다."""
    if value is None or value == "":
        return "—"
    s = str(value)
    return f"`` {s} ``" if "`" in s else f"`{s}`"


def as_dict(value, r: Report, where: str, *, nullable: bool = False) -> dict:
    if isinstance(value, dict):
        return value
    if value is None and not nullable:
        r.problem(where, "객체가 없음")
    elif value is not None:
        r.problem(where, f"객체여야 하는데 {type(value).__name__}")
    return {}


def as_list(value, r: Report, where: str, *, required: bool = True) -> list:
    if isinstance(value, list):
        return value
    if value is not None or required:
        r.problem(where, "배열이 없음" if value is None else f"배열이어야 하는데 {type(value).__name__}")
    return []


def fmt_evidence(ev, r: Report, where: str) -> str:
    """evidence 하나 → `파일:라인`"""
    if not isinstance(ev, dict) or not ev.get("file"):
        r.problem(where, "evidence에 file이 없음")
        return "?"
    loc = f"{ev['file']}:{ev['lines']}" if ev.get("lines") else str(ev["file"])
    if not ev.get("lines"):
        r.problem(where, "evidence에 lines가 없음")
    return code(loc)


def fmt_evidence_list(evs, r: Report, where: str) -> str:
    if isinstance(evs, dict):  # 단일 evidence로 적은 경우도 읽어 준다
        return fmt_evidence(evs, r, where)
    items = as_list(evs, r, where)
    if not items:
        return "?"
    return "<br>".join(fmt_evidence(ev, r, f"{where}[{i}]") for i, ev in enumerate(items))


# ---------------------------------------------------------------- api_contracts
def _endpoint(c: dict) -> str:
    return f"{c.get('method') or '?'} {c.get('path_normalized') or '?'}"


def _detail_bullets(c: dict, r: Report, where: str) -> list[str]:
    """매칭 안 된 항목 하나의 상세 (caller/callee가 null일 수 있다)"""
    out: list[str] = []
    caller = as_dict(c.get("caller"), r, f"{where}.caller", nullable=True)
    callee = as_dict(c.get("callee"), r, f"{where}.callee", nullable=True)
    if caller:
        ev = fmt_evidence_list(caller.get("evidence"), r, f"{where}.caller.evidence").replace("<br>", ", ")
        out.append(
            f"- 호출부 ({caller.get('stack_id', '?')}, client {code(caller.get('client_id'))}): {ev}"
        )
        out.append(f"  - 호출부에 적힌 경로: {code(c.get('path_raw_caller'))}")
        if caller.get("note"):
            out.append(f"  - {caller['note']}")
    if callee:
        ev = fmt_evidence(callee.get("evidence"), r, f"{where}.callee.evidence")
        out.append(f"- 핸들러 ({callee.get('stack_id', '?')}): {code(callee.get('handler_symbol'))} {ev}")
        out.append(f"  - 라우트에 적힌 경로: {code(c.get('path_raw_callee'))}")
        if callee.get("note"):
            out.append(f"  - {callee['note']}")
    if not caller and not callee:
        out.append("- caller와 callee가 모두 비어 있음")
    params = _params(c, r, where)
    if params:
        out.append(f"- 파라미터: {params}")
    out.append(f"- confidence: {c.get('confidence', '?')}")
    return out


def _params(c: dict, r: Report, where: str) -> str:
    bits = []
    path_params = as_list(c.get("path_params"), r, f"{where}.path_params", required=False)
    if path_params:
        names = [
            f"{p.get('name_caller') or '—'}→{p.get('name_callee') or '—'}"
            for p in path_params
            if isinstance(p, dict)
        ]
        bits.append("경로 " + ", ".join(names))
    query_params = as_list(c.get("query_params"), r, f"{where}.query_params", required=False)
    if query_params:
        names = [f"{q.get('name', '?')}({q.get('side', '?')})" for q in query_params if isinstance(q, dict)]
        bits.append("쿼리 " + ", ".join(names))
    return " / ".join(bits)


def render_api_contracts(doc: dict, r: Report) -> None:
    meta = as_dict(doc.get("meta"), r, "meta")
    clients = []
    for i, c in enumerate(as_list(doc.get("clients"), r, "clients")):
        if isinstance(c, dict):
            clients.append(c)
        else:
            r.problem(f"clients[{i}]", "객체가 아니라서 건너뜀")
    raw_contracts = as_list(doc.get("contracts"), r, "contracts")
    contracts: list[tuple[int, dict]] = []
    for i, c in enumerate(raw_contracts):
        if isinstance(c, dict):
            contracts.append((i, c))
        else:
            r.problem(f"contracts[{i}]", "객체가 아니라서 건너뜀")
    unknowns = as_list(doc.get("unknowns"), r, "unknowns", required=False)

    groups: dict[str, list[tuple[int, dict]]] = {s: [] for s in MATCH_STATUSES}
    other: list[tuple[int, dict]] = []
    for i, c in contracts:
        status = c.get("match_status")
        if status in groups:
            groups[status].append((i, c))
        else:
            other.append((i, c))
            r.problem(f"contracts[{i}].match_status", f"알 수 없는 값 {status!r}")

    # 1. 요약 — summary 필드를 믿지 않고 직접 센다
    r.add("# API 계약", "")
    scope = as_list(meta.get("scope"), r, "meta.scope")
    directions = ", ".join(
        f"{d.get('caller_stack_id') or '?'} → {d.get('callee_stack_id') or '(레포에 없음)'}"
        for d in scope
        if isinstance(d, dict)
    )
    r.add(
        f"- 분석 방향: {directions or '?'}",
        f"- 커밋: {code(meta.get('commit'))} / 분석 시각: {meta.get('analyzed_at', '?')}"
        f" / 생성: {meta.get('generated_by', '?')}",
        "",
        "## 요약",
        "",
    )
    r.table(
        ["총 계약", "matched", "caller_only", "callee_only", "ambiguous", "클라이언트"],
        [[len(contracts)] + [len(groups[s]) for s in MATCH_STATUSES] + [len(clients)]],
    )
    summary = as_dict(doc.get("summary"), r, "summary")
    by_status = as_dict(summary.get("by_match_status"), r, "summary.by_match_status") if summary else {}
    if summary and summary.get("total") != len(contracts):
        r.problem("summary.total", f"{summary.get('total')!r}로 적혀 있으나 실제 계약은 {len(contracts)}건")
    for s in MATCH_STATUSES:
        if by_status and by_status.get(s) != len(groups[s]):
            r.problem(f"summary.by_match_status.{s}", f"{by_status.get(s)!r}로 적혀 있으나 실제는 {len(groups[s])}건")
    unresolved = [c for c in clients if c.get("base_url_resolved") is None]
    if unresolved:
        names = ", ".join(code(c.get("id")) for c in unresolved)
        r.add(f"baseURL을 확정하지 못한 클라이언트 {len(unresolved)}개: {names} — 아래 클라이언트 표 참고", "")

    # 2. 매칭 안 된 것
    r.add("## 매칭 안 된 것", "")
    if not (groups["caller_only"] or groups["callee_only"] or groups["ambiguous"] or other):
        r.add("없음.", "")
    sections = [
        ("caller_only", "caller_only — 부르는데 받는 쪽에 라우트가 없음"),
        ("callee_only", "callee_only — 라우트는 있는데 부르는 곳을 못 찾음"),
        ("ambiguous", "ambiguous — 후보가 여럿이라 확정하지 못함"),
    ]
    for status, title in sections:
        items = groups[status]
        if not items:
            continue
        r.add(f"### {title} ({len(items)})", "")
        for i, c in items:
            where = f"contracts[{i}]"
            r.add(f"#### {code(_endpoint(c))}", "", f"{c.get('id', '?')}", "")
            r.add(*_detail_bullets(c, r, where))
            if status == "ambiguous":
                cands = as_list(c.get("candidates"), r, f"{where}.candidates")
                r.add(f"- 후보 {len(cands)}개:")
                for k, cand in enumerate(cands):
                    cw = f"{where}.candidates[{k}]"
                    cand = as_dict(cand, r, cw)
                    bits = [f"  {k + 1}. [{cand.get('side', '?')}]", code(cand.get("path_raw"))]
                    if cand.get("symbol"):
                        bits.append(code(cand["symbol"]))
                    bits.append(fmt_evidence(cand.get("evidence"), r, f"{cw}.evidence"))
                    if cand.get("note"):
                        bits.append(f"— {cand['note']}")
                    r.add(" ".join(bits))
            r.add("")
    if other:
        r.add(f"### match_status를 읽을 수 없는 항목 ({len(other)})", "")
        for i, c in other:
            r.add(f"#### {code(_endpoint(c))}", "", f"{c.get('id', '?')}", "")
            r.add(*_detail_bullets(c, r, f"contracts[{i}]"), "")

    # 3. matched
    r.add(f"## 매칭된 것 ({len(groups['matched'])})", "")
    if groups["matched"]:
        rows = []
        for i, c in groups["matched"]:
            where = f"contracts[{i}]"
            caller = as_dict(c.get("caller"), r, f"{where}.caller")
            callee = as_dict(c.get("callee"), r, f"{where}.callee")
            handler = code(callee.get("handler_symbol")) if callee else "?"
            if callee:
                handler += "<br>" + fmt_evidence(callee.get("evidence"), r, f"{where}.callee.evidence")
            basis = c.get("match_basis") or "?"
            notes = [n for n in (caller.get("note"), callee.get("note")) if n]
            rows.append([
                c.get("method") or "?",
                code(c.get("path_normalized")),
                fmt_evidence_list(caller.get("evidence"), r, f"{where}.caller.evidence") if caller else "?",
                handler,
                f"{c.get('confidence', '?')} ({basis})",
                "<br>".join(notes),
            ])
        r.table(["메서드", "경로", "프론트 위치", "백엔드 핸들러", "confidence (근거)", "비고"], rows)
    else:
        r.add("없음.", "")

    # 4. clients
    r.add(f"## 클라이언트 ({len(clients)})", "")
    if clients:
        rows = []
        for i, c in enumerate(clients):
            resolved = c.get("base_url_resolved")
            if resolved is None:
                resolved_cell = "**미확정**"
            else:
                resolved_cell = code(resolved) if resolved else "(접두 경로 없음)"
            notes = [c["base_url_note"]] if c.get("base_url_note") else []
            for k, m in enumerate(as_list(c.get("modifiers"), r, f"clients[{i}].modifiers", required=False)):
                if isinstance(m, dict):
                    ev = fmt_evidence(m.get("evidence"), r, f"clients[{i}].modifiers[{k}].evidence")
                    notes.append(f"[{m.get('kind', '?')}] {m.get('effect', '?')} {ev}")
            rows.append([
                code(c.get("id")),
                c.get("stack_id", "?"),
                code(c.get("name")),
                code(c.get("base_url_literal")),
                resolved_cell,
                fmt_evidence(c.get("evidence"), r, f"clients[{i}].evidence"),
                "<br>".join(notes),
            ])
        r.table(["id", "스택", "이름", "baseURL (코드 표현)", "baseURL (확정값)", "정의 위치", "비고"], rows)
    else:
        r.add("없음.", "")

    # 5. unknowns
    r.add(f"## 확인하지 못한 것 ({len(unknowns)})", "")
    if unknowns:
        for i, u in enumerate(unknowns):
            u = as_dict(u, r, f"unknowns[{i}]")
            if u:
                r.add(f"- **{u.get('topic', '?')}** — {u.get('reason', '?')}")
        r.add("")
    else:
        r.add("없음.", "")


FORMATTERS = {
    "api_contracts": render_api_contracts,
}


# ---------------------------------------------------------------- main
def render(file_path: Path) -> tuple[str | None, str | None]:
    """(마크다운, 오류 메시지) 중 하나만 채워서 반환"""
    kind = detect_kind(file_path)
    if kind is None:
        return None, f"산출물 종류를 판별할 수 없음: {file_path.name} (.handover/ 아래 산출물 파일명이 아님)"
    formatter = FORMATTERS.get(kind)
    if formatter is None:
        supported = ", ".join(sorted(FORMATTERS))
        return None, f"지원하지 않음: {kind} 산출물은 아직 포맷터가 없다 (지원: {supported})"
    try:
        doc = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return None, f"파일을 읽을 수 없음: {e}"
    except json.JSONDecodeError as e:
        return None, f"JSON 파싱 실패: {e}"

    r = Report()
    if not isinstance(doc, dict):
        r.problem("(root)", f"최상위가 객체여야 하는데 {type(doc).__name__}")
        doc = {}
    try:
        formatter(doc, r)
    except Exception as e:  # 예상하지 못한 형태 — 여기까지 만든 출력은 살린다
        r.problem("(renderer)", f"렌더링이 중간에 중단됨: {type(e).__name__}: {e}")
    return r.text(), None


def main() -> int:
    parser = argparse.ArgumentParser(description=".handover/ JSON 산출물을 마크다운으로 렌더링")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--out", type=Path, help="마크다운을 쓸 경로 (없으면 표준출력)")
    args = parser.parse_args()

    file_path = args.json_path.resolve()
    if not file_path.is_file():
        print(f"파일이 없음: {args.json_path}", file=sys.stderr)
        return 1
    markdown, error = render(file_path)
    if error:
        print(error, file=sys.stderr)
        return 1
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
