#!/usr/bin/env python3
"""spec JSON(CI 가 올리는 것) + YAML(사람이 쓰는 것) → 사이트가 읽는 단일 번들.

정적 사이트는 파서 없이 JSON 만 읽는다. 그렇다고 사람이 편집하는 파일까지 JSON 으로
두면 주석을 못 달고 diff 가 지저분해지므로, 저장은 YAML 로 하고 배포 시점에 변환한다.

소유권 규칙이 이 파일의 구조를 결정한다.

    spec/       기계가 만든다. 배포가 통째로 덮어쓴다
    overlay/    사람이 쓴다. 배포가 건드리지 않는다
    comments/   사람이 쓴다. 배포가 건드리지 않는다

둘은 렌더링 시점에 앵커로 합쳐지므로 서로 덮어쓸 일이 없다.
"""
import json
import pathlib
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML 이 필요합니다: pip install pyyaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "_site" / "data"


def load_yaml(path: pathlib.Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def git(*args: str) -> str:
    """git 출력. 저장소가 아니거나 실패하면 빈 문자열."""
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def collect_specs() -> dict:
    """spec/{project}/{env}.json → { project: { env: spec } }

    스펙은 손대지 않고 그대로 담는다. OpenAPI 를 화면 모델로 펴는 일은 브라우저가 한다.
    여기서 미리 펴 두면 스펙이 바뀔 때마다 이 스크립트도 같이 고쳐야 한다.
    """
    result: dict[str, dict] = {}
    base = ROOT / "spec"
    if not base.exists():
        return result

    for path in sorted(base.rglob("*.json")):
        project = path.parent.name
        with path.open(encoding="utf-8") as f:
            result.setdefault(project, {})[path.stem] = json.load(f)
    return result


def spec_meta(project: str, env: str) -> dict:
    """그 스펙 파일이 마지막으로 갱신된 커밋. '이 문서가 최신인가'의 근거가 된다."""
    out = git("log", "-1", "--format=%h\t%cs", "--", f"spec/{project}/{env}.json")
    if not out:
        return {"sha": "—", "at": "—"}
    sha, at = out.split("\t", 1)
    return {"sha": sha, "at": at}


def collect(kind: str) -> dict:
    """overlay|comments/{project}/{endpoint}.yaml → { project: { endpoint: 내용 } }"""
    result: dict[str, dict] = {}
    base = ROOT / kind
    if not base.exists():
        return result

    for path in sorted(base.rglob("*.yaml")):
        project = path.parent.name
        result.setdefault(project, {})[path.stem] = load_yaml(path)
    return result


def comments_meta() -> dict:
    """댓글 파일의 blob SHA. 저장할 때 충돌 판정에 쓴다.

    화면이 만들어진 시점의 파일과 저장 직전의 파일이 같은지 비교하는 기준이다.
    """
    meta: dict[str, dict] = {}
    base = ROOT / "comments"
    if not base.exists():
        return meta

    for path in sorted(base.rglob("*.yaml")):
        rel = path.relative_to(ROOT).as_posix()
        meta[f"{path.parent.name}/{path.stem}"] = {"sha": git("hash-object", rel)}
    return meta


def main() -> None:
    config = load_yaml(ROOT / "config.yaml")
    registry = load_yaml(ROOT / "projects.yaml")
    specs = collect_specs()

    # 레지스트리에 있는데 스펙이 없으면 아직 배포되지 않은 것이다. 조용히 넘기지 않는다 —
    # 그냥 두면 등록한 환경의 문서가 빈 화면으로 보이고 원인을 찾기 어렵다.
    for project in registry.get("projects", []):
        pid = project["id"]
        for env in project.get("envs", {}):
            if env not in specs.get(pid, {}):
                print(f"  경고: {pid}/{env} 스펙이 없습니다 (spec/{pid}/{env}.json)")

    bundle = {
        "config": config,
        "buildSha": git("rev-parse", "--short", "HEAD") or "—",
        "projects": registry.get("projects", []),
        "specs": specs,
        "specMeta": {
            pid: {env: spec_meta(pid, env) for env in envs}
            for pid, envs in specs.items()
        },
        "overlay": collect("overlay"),
        "comments": collect("comments"),
        "commentsMeta": comments_meta(),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "bundle.json"
    with target.open("w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))

    endpoints = sum(
        len(spec.get("paths", {})) for envs in specs.values() for spec in envs.values()
    )
    print(
        f"번들 생성: {target.relative_to(ROOT)} "
        f"({target.stat().st_size // 1024}KB, 프로젝트 {len(bundle['projects'])}개, "
        f"경로 {endpoints}개)"
    )


if __name__ == "__main__":
    main()
