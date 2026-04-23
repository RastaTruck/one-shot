"""素材库管理 — 分镜 Agent 用它做"选用现有素材还是临时生成"的判断。

核心数据源: config/asset_catalog.json
  {
    "backgrounds": [{"id": "BG_OFFICE_01", "file": "...", "tags": [...], "description": "..."}],
    "characters":  [...],
    "elements":    [...],
    "expressions": [                     # 用户预制的透明 WebM/MOV 表情动图库
      {
        "id": "EXPR_SHOCK_01",
        "file": "data/assets/expressions/shock_01.webm",
        "description": "布布惊讶捂嘴",
        "meta": {
          "scene_tags": ["震惊"],       # 闭集合 15 个，见 shot_table.SCENE_TAGS
          "intensity": 2,                 # 1~3
          "duration": 2.4,                # 秒
          "has_alpha": true,
          "character": "一二布布"
        }
      }
    ]
  }

expressions 检索策略: 场景标签精确匹配 + intensity 加权（见 search_expressions）
其它类检索: tags/description 关键词匹配
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "asset_catalog.json"
ASSETS_ROOT = Path(__file__).resolve().parents[1] / "data" / "assets"


AssetKind = Literal["backgrounds", "characters", "elements", "expressions"]


@dataclass
class Asset:
    id: str
    file: str                       # 相对仓库根的文件路径
    tags: list[str] = field(default_factory=list)
    description: str = ""
    kind: str = ""                  # backgrounds / characters / ...
    style: str = ""                 # 风格 profile，例如 MBE_v1
    meta: dict = field(default_factory=dict)

    @property
    def absolute_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / self.file


class AssetLibrary:
    def __init__(self, catalog_path: Path = CATALOG_PATH) -> None:
        self.catalog_path = catalog_path
        self._catalog: dict[str, list[Asset]] = {
            "backgrounds": [],
            "characters": [],
            "elements": [],
            "expressions": [],
        }
        self.load()

    # ---------- 读写 ----------

    def load(self) -> None:
        if not self.catalog_path.exists():
            # 空目录初始化
            self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            self.catalog_path.write_text(
                json.dumps(
                    {"backgrounds": [], "characters": [], "elements": [], "expressions": []},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        for kind in self._catalog.keys():
            items = raw.get(kind, [])
            self._catalog[kind] = [
                Asset(
                    id=item["id"],
                    file=item["file"],
                    tags=item.get("tags", []),
                    description=item.get("description", ""),
                    style=item.get("style", ""),
                    meta=item.get("meta", {}),
                    kind=kind,
                )
                for item in items
            ]

    def save(self) -> None:
        payload = {
            kind: [
                {
                    "id": a.id,
                    "file": a.file,
                    "tags": a.tags,
                    "description": a.description,
                    "style": a.style,
                    "meta": a.meta,
                }
                for a in assets
            ]
            for kind, assets in self._catalog.items()
        }
        self.catalog_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 查询 ----------

    def get(self, asset_id: str) -> Asset | None:
        for assets in self._catalog.values():
            for a in assets:
                if a.id == asset_id:
                    return a
        return None

    def all(self, kind: AssetKind) -> list[Asset]:
        return list(self._catalog[kind])

    def search(
        self,
        kind: AssetKind,
        keywords: Iterable[str] | None = None,
    ) -> list[Asset]:
        """简单打分检索：每个 keyword 命中 tag/description 得 1 分，高到低排序。"""
        assets = self._catalog[kind]
        kws = [k.lower() for k in (keywords or []) if k]
        if not kws:
            return list(assets)

        scored: list[tuple[int, Asset]] = []
        for a in assets:
            text = " ".join([a.description] + a.tags).lower()
            score = sum(1 for k in kws if k in text)
            if score > 0:
                scored.append((score, a))
        scored.sort(key=lambda x: -x[0])
        return [a for _, a in scored]

    def search_expressions(
        self,
        scene_tags: Iterable[str],
        intensity: int | None = None,
        character: str | None = None,
    ) -> list[Asset]:
        """表情库专用检索。按 scene_tags 命中数排序，intensity 接近者加分。

        - scene_tags: 必须全部来自 shot_table.SCENE_TAGS 闭集合
        - intensity: 目标强度 (1~3)，None = 不要求
        - character: 限定角色（如"一二布布"），None = 不限
        """
        wanted = set(scene_tags)
        if not wanted:
            return []
        scored: list[tuple[float, Asset]] = []
        for a in self._catalog["expressions"]:
            meta_tags = set(a.meta.get("scene_tags", []))
            hits = wanted & meta_tags
            if not hits:
                continue
            if character and a.meta.get("character") != character:
                continue
            score = float(len(hits))
            if intensity is not None and "intensity" in a.meta:
                score += max(0.0, 1.0 - 0.4 * abs(intensity - a.meta["intensity"]))
            scored.append((score, a))
        scored.sort(key=lambda x: -x[0])
        return [a for _, a in scored]

    # ---------- 追加 ----------

    def add(
        self,
        kind: AssetKind,
        asset_id: str,
        file_path: str | Path,
        tags: list[str] | None = None,
        description: str = "",
        style: str = "",
        meta: dict | None = None,
    ) -> Asset:
        # 文件路径标准化为"相对仓库根"
        p = Path(file_path)
        if p.is_absolute():
            repo_root = Path(__file__).resolve().parents[1]
            try:
                p = p.relative_to(repo_root)
            except ValueError:
                pass
        asset = Asset(
            id=asset_id,
            file=str(p),
            tags=tags or [],
            description=description,
            style=style,
            meta=meta or {},
            kind=kind,
        )
        # 去重 by id
        self._catalog[kind] = [a for a in self._catalog[kind] if a.id != asset_id]
        self._catalog[kind].append(asset)
        self.save()
        return asset

    def add_expression(
        self,
        asset_id: str,
        file_path: str | Path,
        scene_tags: list[str],
        intensity: int = 2,
        duration: float | None = None,
        character: str = "",
        description: str = "",
    ) -> Asset:
        """表情库专用登记 (scene_tags 存进 meta，跟 add() 分开避免误用)。"""
        meta = {
            "scene_tags": scene_tags,
            "intensity": intensity,
            "has_alpha": True,
        }
        if duration is not None:
            meta["duration"] = duration
        if character:
            meta["character"] = character
        return self.add(
            "expressions",
            asset_id=asset_id,
            file_path=file_path,
            tags=scene_tags,
            description=description,
            meta=meta,
        )

    def add_expression_batch(
        self,
        directory: str | Path,
        entries: list[dict],
    ) -> list[Asset]:
        """批量导入一个目录下的表情动图。

        entries: [{"file": "shock_01.webm", "id": "EXPR_SHOCK_01",
                   "scene_tags": ["震惊"], "intensity": 2,
                   "duration": 2.4, "character": "一二布布", "description": "..."}]
        file 可以是相对 directory 的文件名，也可以是绝对路径。
        """
        directory = Path(directory)
        out: list[Asset] = []
        for e in entries:
            fp = Path(e["file"])
            if not fp.is_absolute():
                fp = directory / fp
            out.append(
                self.add_expression(
                    asset_id=e["id"],
                    file_path=fp,
                    scene_tags=e["scene_tags"],
                    intensity=e.get("intensity", 2),
                    duration=e.get("duration"),
                    character=e.get("character", ""),
                    description=e.get("description", ""),
                )
            )
        return out

    # ---------- 给分镜 Agent 喂的 context ----------

    def as_context_for_llm(self, max_per_kind: int = 50) -> str:
        """生成给 LLM 的紧凑目录字符串，让它知道有哪些素材可以选。"""
        lines: list[str] = []
        for kind_name, label in [
            ("backgrounds", "背景"),
            ("expressions", "表情动图 (预制)"),
            ("elements", "画框内元素 (复用库)"),
        ]:
            assets = self._catalog[kind_name][:max_per_kind]
            if not assets:
                lines.append(f"## {label} (库中暂无)")
                lines.append("")
                continue
            lines.append(f"## {label}")
            for a in assets:
                if kind_name == "expressions":
                    scene = "/".join(a.meta.get("scene_tags", []))
                    inten = a.meta.get("intensity", "-")
                    lines.append(f"- {a.id}: {a.description} [场景:{scene} 强度:{inten}]")
                else:
                    tag_str = "/".join(a.tags) if a.tags else ""
                    lines.append(f"- {a.id}: {a.description} [{tag_str}]")
            lines.append("")
        return "\n".join(lines)

    def stats(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._catalog.items()}


if __name__ == "__main__":
    lib = AssetLibrary()
    print(f"[asset_library] initial stats: {lib.stats()}")

    lib.add(
        "backgrounds",
        "BG_OFFICE_01",
        "data/assets/backgrounds/BG_OFFICE_01.png",
        tags=["办公室", "书桌", "电脑"],
        description="明亮办公桌",
    )
    lib.add_expression(
        "EXPR_SHOCK_01",
        "data/assets/expressions/shock_01.webm",
        scene_tags=["震惊"],
        intensity=3,
        duration=2.4,
        character="一二布布",
        description="布布惊讶捂嘴",
    )
    lib.add_expression(
        "EXPR_WORRIED_01",
        "data/assets/expressions/worried_01.webm",
        scene_tags=["担忧", "失落"],
        intensity=2,
        duration=3.0,
        character="一二布布",
        description="布布皱眉忧虑",
    )
    print(f"[asset_library] after add: {lib.stats()}")
    hits = lib.search_expressions(["震惊"])
    print(f"[asset_library] 震惊 -> {[h.id for h in hits]}")
    hits = lib.search_expressions(["担忧", "失落"], intensity=2)
    print(f"[asset_library] 担忧/失落 @ intensity=2 -> {[h.id for h in hits]}")

    print("\n[asset_library] context for LLM:\n")
    print(lib.as_context_for_llm())
