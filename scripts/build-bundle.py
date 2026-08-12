#!/usr/bin/env python3
"""content/*.md(문서) + comments/*.yaml(댓글) → 사이트가 읽는 단일 번들.

정적 사이트는 파서 없이 JSON 만 읽는다. 마크다운 해석도 여기서 끝내고
브라우저에는 블록 배열만 넘긴다.
"""
import html
import json
import pathlib
import re
import subprocess
import sys

# YAML 파일을 읽기 위해 PyYAML을 불러온다.
try:
    import yaml
except ImportError:
    sys.exit("PyYAML 이 필요합니다: pip install pyyaml")
# 현재 스크립트 위치를 기준으로 저장소 루트 경로를 찾는다.
ROOT = pathlib.Path(__file__).resolve().parent.parent
# 생성할 bundle.json의 저장 디렉터리 경로를 지정한다.
OUT = ROOT / "_site" / "data"
# Git 명령을 실행하고 결과를 반환한다.
# 실패하면 빈 문자열을 반환한다.
def git(*args: str) -> str:
    """git 출력. 저장소가 아니거나 실패하면 빈 문자열."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

# 제목을 댓글 앵커에 사용할 slug로 변환한다.
def slugify(text: str) -> str:
    """제목 → 앵커 slug. 한글은 그대로 유지한다."""
    s = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")

# 문단과 제목의 인라인 서식을 HTML로 변환한다.
def inline(text: str) -> str:
    """HTML 이스케이프 후 코드와 굵은 글씨를 처리한다."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

# Markdown을 제목, 문단, 목록, 코드블록의 블록 배열로 변환한다.
def parse_markdown(text: str) -> list[dict]:
    """지원할 Markdown 문법만 처리하는 간단한 변환기."""
    lines = text.splitlines()
    blocks: list[dict] = []
    buf: list[str] = []
    i = 0

    # 모아 둔 일반 문단 줄을 하나의 p 블록으로 만든다.
    def flush() -> None:
        if buf:
            blocks.append({"type": "p", "html": inline(" ".join(buf))})
            buf.clear()

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            flush()
            i += 1
            code = []

            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1

            blocks.append({"type": "code", "text": "\n".join(code)})

        elif line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            title = line[level:].strip()
            block = {"type": f"h{level}", "html": inline(title)}

            if level >= 2:
                block["anchor"] = f"sec.{slugify(title)}"

            blocks.append(block)

        elif line.startswith("- "):
            flush()
            items = []

            while i < len(lines) and lines[i].startswith("- "):
                items.append(inline(lines[i][2:].strip()))
                i += 1

            blocks.append({"type": "ul", "items": items})
            continue

        elif not line.strip():
            flush()

        else:
            buf.append(line.strip())

        i += 1

    flush()
    return blocks

# content/*.md 파일을 읽어 문서 목록으로 만든다.
def collect_docs() -> list[dict]:
    docs = []

    for path in sorted((ROOT / "content").glob("*.md")):
        blocks = parse_markdown(path.read_text(encoding="utf-8"))

        title = next(
            (b["html"] for b in blocks if b["type"] == "h1"),
            path.stem,
        )

        out = git(
            "log",
            "-1",
            "--format=%h\t%cs",
            "--",
            f"content/{path.name}",
        )

        sha, at = out.split("\t", 1) if out else ("—", "—")

        docs.append(
            {
                "id": path.stem,
                "title": title,
                "blocks": blocks,
                "rev": {"sha": sha, "at": at},
            }
        )

    return docs

# comments/*.yaml 파일과 각 파일의 Git blob SHA를 수집한다.
def collect_comments() -> tuple[dict, dict]:
    """댓글 목록과 Git blob SHA를 함께 반환한다."""
    threads: dict[str, list] = {}
    meta: dict[str, dict] = {}
    base = ROOT / "comments"

    if not base.exists():
        return threads, meta

    for path in sorted(base.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            threads[path.stem] = yaml.safe_load(f) or []

        meta[path.stem] = {
            "sha": git("hash-object", f"comments/{path.name}")
        }

    return threads, meta

# 번들 생성에 필요한 데이터를 모아 JSON 파일로 저장한다.
def main() -> None:
    # config.yaml을 읽는다.
    config = yaml.safe_load(
        (ROOT / "config.yaml").read_text(encoding="utf-8")
    ) or {}

    # 문서와 댓글을 수집한다.
    docs = collect_docs()
    comments, meta = collect_comments()

    # 문서가 없는데 댓글 파일만 있는 경우 경고한다.
    ids = {doc["id"] for doc in docs}
    for doc_id in comments:
        if doc_id not in ids:
            print(f"경고: comments/{doc_id}.yaml에 대응하는 문서가 없습니다")

    # 최종 JSON 구조를 만든다.
    bundle = {
        "config": config,
        "buildSha": git("rev-parse", "--short", "HEAD") or "—",
        "docs": docs,
        "comments": comments,
        "commentsMeta": meta,
    }

    # 출력 디렉터리를 만들고 bundle.json을 저장한다.
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "bundle.json"

    with target.open("w", encoding="utf-8") as f:
        json.dump(
            bundle,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # 생성 결과를 출력한다.
    count = sum(len(items) for items in comments.values())
    print(f"번들 생성: {target.relative_to(ROOT)} (문서 {len(docs)}개, 댓글 {count}개)")

# 이 파일을 직접 실행할 때만 main()을 호출한다.
if __name__ == "__main__":
    main()
