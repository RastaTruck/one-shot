#!/usr/bin/env python3
"""鸟瞰站数据生成器 · 扫描项目输出 public/audit-data.json

用法（项目根目录运行）：
    python3 scripts/build_audit_data.py

输出：
    public/audit-data.json  ← audit.html 读这个

设计原则（防 #DK 陈旧产物）：
- 每个 entity 强制有 lifecycle 字段（active / deprecated / removed）
- 扫历史 commit 找 180d 内被删的文件 → 标 removed + 检查还有没有引用
- 扫 "已废 / deprecated / legacy / V[12]" 注释 → 标 deprecated
- 计算 orphans（没人依赖 + deprecated 但仍被引用 + removed 但仍被引用）
- 按管线分桶（news / outhan / yierbubu / forge / shared / infra）
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 管线归属判定：路径前缀 → 管线 id
PIPELINE_BY_PREFIX = [
    ("pipeline/agents/news_", "news"),
    ("pipeline/news_", "news"),
    ("web/templates/step_news_", "news"),
    ("web/templates/_news_", "news"),
    ("pipeline/agents/outhan_", "outhan"),
    ("pipeline/outhan_", "outhan"),
    ("web/templates/step_outhan_", "outhan"),
    ("web/templates/_outhan_", "outhan"),
    ("pipeline/hotspot_forge/", "forge"),
    ("web/templates/forge", "forge"),
    ("web/templates/inspiration", "forge"),
    ("pipeline/tools/", "shared"),
    ("pipeline/state.py", "infra"),
    ("pipeline/asset_library.py", "shared"),
    ("pipeline/shot_table.py", "yierbubu"),
    ("pipeline/shot_template_library.py", "shared"),
    ("pipeline/flow.py", "infra"),
    ("pipeline/mocks/", "infra"),
    ("pipeline/review_ui/", "infra"),
    ("web/app.py", "infra"),
    ("web/templates/base.html", "infra"),
    ("web/templates/_", "infra"),
    ("web/templates/projects.html", "infra"),
    ("web/templates/project_birds_eye.html", "infra"),
    ("web/templates/library", "shared"),
    ("config/news_", "news"),
    ("config/outhan_", "outhan"),
    ("config/", "shared"),
    ("scripts/", "infra"),
    ("pipeline/agents/", "yierbubu"),  # 兜底 · 老 yierbubu agent
    ("web/templates/step_", "yierbubu"),  # 兜底 · 老 yierbubu template
]


def pipeline_of(rel: str) -> str:
    for prefix, p in PIPELINE_BY_PREFIX:
        if rel.startswith(prefix):
            return p
    return "infra"


def kind_of(rel: str) -> str:
    if rel.endswith(".html"):
        return "template"
    if rel.startswith("pipeline/tools/"):
        return "tool"
    if rel.startswith("config/"):
        return "config"
    if rel.startswith("scripts/"):
        return "script"
    if rel == "web/app.py":
        return "route_hub"
    if rel.endswith("_meta.py"):
        return "schema"
    if rel.endswith(".py"):
        return "module"
    return "other"


# 已知"已废" / deprecated / legacy / V1/V2 标记 → 注释里包含这些关键词的字段名
DEPRECATED_PATTERNS = [
    re.compile(r"#.*已[废退弃]"),
    re.compile(r"#.*deprecated", re.IGNORECASE),
    re.compile(r"#.*legacy"),
    re.compile(r"\[AI-NOTE\].*已删"),
    re.compile(r"V[12]\s*·"),
]


def scan_lifecycle(path: Path) -> tuple[str, list[str]]:
    """读文件首 200 行，看 module-level 注释里有没有 deprecated/legacy 信号。

    返回 (lifecycle, signals)：
      lifecycle: active / deprecated / mixed
      signals: 命中的关键行内容（截短）
    """
    if not path.exists() or path.is_dir():
        return "active", []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "active", []
    head = "\n".join(text.splitlines()[:200])
    sigs = []
    for pat in DEPRECATED_PATTERNS:
        for m in pat.finditer(head):
            line = m.group(0).strip()[:120]
            if line not in sigs:
                sigs.append(line)
    # 整文件有 _legacy / _v1 函数名也算
    if re.search(r"\bdef\s+\w+_legacy\b|\bclass\s+\w+V1\b|\b_LEGACY\b", text):
        sigs.append("contains _legacy/_v1 symbol")
    if not sigs:
        return "active", []
    if "已删" in " ".join(sigs):
        return "deprecated", sigs
    return "deprecated" if sigs else "active", sigs


def build_dependency_map(py_files: list[Path]) -> dict[str, set[str]]:
    """简陋的 import 关系扫描——只识别 `from pipeline.X import` / `import pipeline.X`
    返回 {entity_id: {它 import 的 entity_id 集合}}。"""
    import_re = re.compile(
        r"^\s*(?:from\s+(pipeline\.[\w\.]+|web\.[\w\.]+)\s+import|"
        r"import\s+(pipeline\.[\w\.]+|web\.[\w\.]+))",
        re.MULTILINE,
    )
    deps: dict[str, set[str]] = {}
    for p in py_files:
        rel = str(p.relative_to(ROOT))
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        ids = set()
        for m in import_re.finditer(text):
            module = m.group(1) or m.group(2)
            if not module:
                continue
            # pipeline.tools.fish_audio_client → pipeline/tools/fish_audio_client.py
            ids.add(module.replace(".", "/") + ".py")
        deps[rel] = ids
    return deps


def scan_legacy_endpoints() -> list[dict]:
    """grep web/app.py 找 legacy / _v[12] 命名的路由函数。"""
    app_py = ROOT / "web/app.py"
    if not app_py.exists():
        return []
    text = app_py.read_text(encoding="utf-8", errors="ignore")
    out = []
    # 匹配 @app.<method>(...) 后面的 async def *_legacy / *_v1 / *_v2
    route_re = re.compile(
        r'@app\.(get|post|put|delete)\("([^"]+)"[^)]*\)\s*\n\s*async def (\w+)',
        re.MULTILINE,
    )
    for m in route_re.finditer(text):
        method, path, fname = m.group(1), m.group(2), m.group(3)
        if "legacy" in fname.lower() or re.search(r"_v[12]\b", fname):
            out.append({"method": method.upper(), "path": path, "function": fname})
    return out


def scan_deprecated_fields() -> list[dict]:
    """扫 *_meta.py 找带 "已废 / deprecated" 注释的字段。"""
    out = []
    for meta_file in (ROOT / "pipeline").glob("*_meta.py"):
        text = meta_file.read_text(encoding="utf-8", errors="ignore")
        rel = str(meta_file.relative_to(ROOT))
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"已[废退弃]|deprecated", line, re.IGNORECASE):
                # 提取字段名（左侧 "field_name":）
                fm = re.match(r'\s*"(\w+)"\s*:', line)
                field = fm.group(1) if fm else line.strip()[:60]
                out.append({"file": rel, "line": i, "field": field, "context": line.strip()[:120]})
    return out


def scan_v_pairs() -> list[dict]:
    """扫代码库找 V1/V2 命名对。"""
    out = []
    for py in (ROOT / "pipeline").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        rel = str(py.relative_to(ROOT))
        # 找 V1 标记 + 同文件 V2 标记
        v1_lines = [(i + 1, ln) for i, ln in enumerate(text.splitlines())
                    if re.search(r"\bV1\b|index\.html.*V1|GSAP|funny_zoo", ln)]
        v2_lines = [(i + 1, ln) for i, ln in enumerate(text.splitlines())
                    if re.search(r"\bV2\b|index_v2|black_gold", ln)]
        if v1_lines and v2_lines:
            out.append({
                "file": rel,
                "v1_count": len(v1_lines),
                "v2_count": len(v2_lines),
                "v1_example": v1_lines[0][1].strip()[:100],
                "v2_example": v2_lines[0][1].strip()[:100],
            })
    return out


def scan_removed_files_180d() -> list[dict]:
    """git log 找 180d 内被删的文件，看是否还有引用。"""
    try:
        result = subprocess.run(
            ["git", "log", "--diff-filter=D", "--since=180.days", "--name-only",
             "--pretty=format:%H|%s|%ai"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    lines = result.stdout.split("\n")
    deletions = []
    current = None
    for ln in lines:
        ln = ln.strip()
        if "|" in ln and len(ln.split("|")) == 3:
            current = ln.split("|")
        elif ln and current:
            commit_hash, msg, date = current
            deletions.append({
                "path": ln,
                "deleted_in": commit_hash[:8],
                "commit_msg": msg[:80],
                "deleted_at": date.split()[0],
            })
    # 检查每个被删的文件名是否还出现在现有代码里
    for d in deletions:
        fname = Path(d["path"]).stem  # 文件名（不含扩展名）
        try:
            g = subprocess.run(
                ["grep", "-rln", fname, "pipeline/", "web/", "scripts/", "config/"],
                cwd=ROOT, capture_output=True, text=True, timeout=10,
            )
            d["still_referenced_in"] = [
                f for f in g.stdout.strip().split("\n") if f
            ] if g.returncode == 0 else []
        except Exception:
            d["still_referenced_in"] = []
    return deletions


def main():
    print(f"[build_audit_data] 扫描 {ROOT} ...")

    # 收集 source files
    py_files = sorted(
        [p for p in ROOT.rglob("*.py")
         if not any(x in p.parts for x in (".git", "__pycache__", "node_modules", ".claude", "data"))]
    )
    html_files = sorted(
        [p for p in (ROOT / "web/templates").rglob("*.html") if p.is_file()]
    )
    config_files = sorted(
        [p for p in (ROOT / "config").rglob("*.json") if p.is_file()]
    )

    print(f"  · Python: {len(py_files)} 个")
    print(f"  · HTML template: {len(html_files)} 个")
    print(f"  · config JSON: {len(config_files)} 个")

    # 依赖关系
    deps = build_dependency_map(py_files)

    # 反向 used_by
    used_by: dict[str, set[str]] = {}
    for src, targets in deps.items():
        for t in targets:
            used_by.setdefault(t, set()).add(src)

    # 构造 entities
    entities = []
    all_files = py_files + html_files + config_files
    for path in all_files:
        rel = str(path.relative_to(ROOT))
        lifecycle, signals = scan_lifecycle(path)
        try:
            lines = sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
        except Exception:
            lines = 0
        entities.append({
            "id": rel,
            "kind": kind_of(rel),
            "pipeline": pipeline_of(rel),
            "lifecycle": lifecycle,
            "lifecycle_signals": signals,
            "lines": lines,
            "depends_on": sorted(deps.get(rel, set())),
            "used_by": sorted(used_by.get(rel, set())),
        })

    # 孤儿检测
    orphans = {
        "no_source": [],     # 没人依赖（叶节点 / dead code 候选）
        "no_sink": [],       # 不依赖任何东西（入口 / 配置）
        "removed_with_refs": [],  # 文件已删但代码里还有引用
        "deprecated_with_refs": [],  # deprecated 但还有 used_by
    }
    for e in entities:
        # no_source: 无 used_by 且不是入口（templates 总会被 app.py 渲染但 grep 抓不到）
        if not e["used_by"] and e["kind"] not in ("template", "route_hub", "config", "script"):
            orphans["no_source"].append({"id": e["id"], "kind": e["kind"], "pipeline": e["pipeline"]})
        if e["lifecycle"] == "deprecated" and e["used_by"]:
            orphans["deprecated_with_refs"].append({
                "id": e["id"], "used_by": e["used_by"][:5],
                "signals": e["lifecycle_signals"][:3],
            })

    # 已删但仍被引用
    removed_files = scan_removed_files_180d()
    for d in removed_files:
        if d.get("still_referenced_in"):
            orphans["removed_with_refs"].append(d)

    # 管线汇总
    pipelines_summary = {}
    for e in entities:
        p = e["pipeline"]
        pipelines_summary.setdefault(p, {
            "id": p,
            "entity_count": 0,
            "total_lines": 0,
            "deprecated_count": 0,
            "kind_breakdown": {},
        })
        pipelines_summary[p]["entity_count"] += 1
        pipelines_summary[p]["total_lines"] += e["lines"]
        if e["lifecycle"] == "deprecated":
            pipelines_summary[p]["deprecated_count"] += 1
        pipelines_summary[p]["kind_breakdown"][e["kind"]] = \
            pipelines_summary[p]["kind_breakdown"].get(e["kind"], 0) + 1

    # lifecycle 信号大全
    lifecycle_signals = {
        "legacy_endpoints": scan_legacy_endpoints(),
        "deprecated_fields": scan_deprecated_fields(),
        "v1_v2_pairs": scan_v_pairs(),
        "removed_files_180d": removed_files,
    }

    # DB 项目数（按 project_type）
    db_stats = {}
    db_path = ROOT / "data" / "pipeline.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cur = conn.execute(
                "SELECT project_type, COUNT(*) FROM videos GROUP BY project_type"
            )
            db_stats = {row[0]: row[1] for row in cur.fetchall()}
            conn.close()
        except Exception as e:
            db_stats = {"error": str(e)}

    # 输出
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(ROOT),
        "summary": {
            "total_entities": len(entities),
            "total_lines": sum(e["lines"] for e in entities),
            "deprecated_count": sum(1 for e in entities if e["lifecycle"] == "deprecated"),
            "orphan_count": len(orphans["no_source"]),
            "db_projects": db_stats,
        },
        "pipelines": list(pipelines_summary.values()),
        "entities": entities,
        "orphans": orphans,
        "lifecycle_signals": lifecycle_signals,
    }

    out_path = ROOT / "public" / "audit-data.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 输出 {out_path}")
    print(f"  总 entities: {out['summary']['total_entities']}")
    print(f"  已废 entities: {out['summary']['deprecated_count']}")
    print(f"  孤儿（no_source）: {out['summary']['orphan_count']}")
    print(f"  已删但仍引用: {len(orphans['removed_with_refs'])}")
    print(f"  legacy endpoints: {len(lifecycle_signals['legacy_endpoints'])}")


if __name__ == "__main__":
    main()
