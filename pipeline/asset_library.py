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
        self._meta: dict = {}
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
        self._meta = raw.get("meta", {})
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

    # ---------- 画风系列分组 ----------

    def list_ip_libraries(self) -> list[dict]:
        """把 characters + expressions 按 character 聚合成 IP 库列表。"""
        meta_libs = (self._meta or {}).get("ip_libraries", {})
        groups: dict[str, dict] = {}
        for ch in self._catalog["characters"]:
            name = ch.meta.get("character") or ch.id
            groups.setdefault(name, {"character_name": name, "cover_file": ch.file,
                                      "cover_asset_id": ch.id, "expressions": []})
        for expr in self._catalog["expressions"]:
            name = expr.meta.get("character") or "其他"
            g = groups.setdefault(name, {"character_name": name, "cover_file": expr.file,
                                          "cover_asset_id": expr.id, "expressions": []})
            g["expressions"].append({
                "id": expr.id, "file": expr.file,
                "description": expr.description,
                "scene_tags": expr.meta.get("scene_tags", []),
            })
        out = []
        for name, g in groups.items():
            # 找到 meta 里对应 library_id（通过 character_name 匹配）
            lib_id = name
            display_name = name
            desc = ""
            for lid, lmeta in meta_libs.items():
                if lmeta.get("character_name") == name:
                    lib_id = lid
                    display_name = lmeta.get("name", name)
                    desc = lmeta.get("desc", "")
                    break
            out.append({
                "id": lib_id,
                "name": display_name,
                "desc": desc,
                "character_name": name,
                "cover_file": g["cover_file"],
                "expression_count": len(g["expressions"]),
                "scene_tags": sorted({t for e in g["expressions"] for t in e["scene_tags"]}),
                "sample_expressions": g["expressions"][:6],
            })
        return out

    def list_element_libraries(self) -> list[dict]:
        """按 asset.style 把 elements 聚合成画风系列。"""
        meta_libs = (self._meta or {}).get("element_libraries", {})
        groups: dict[str, list[Asset]] = {}
        for el in self._catalog["elements"]:
            key = el.style or "其他"
            groups.setdefault(key, []).append(el)
        out = []
        for style_key, assets in groups.items():
            lmeta = meta_libs.get(style_key, {})
            out.append({
                "id": style_key,
                "name": lmeta.get("name", style_key),
                "desc": lmeta.get("desc", ""),
                "style_tags": lmeta.get("style_tags", []),
                "count": len(assets),
                "samples": [{"id": a.id, "file": a.file, "description": a.description}
                             for a in assets[:6]],
            })
        return out

    def element_library_meta(self, library_id: str) -> dict:
        return (self._meta or {}).get("element_libraries", {}).get(library_id, {})

    @property
    def meta(self) -> dict:
        return self._meta

    def save(self) -> None:
        payload: dict = {}
        if self._meta:
            payload["meta"] = self._meta
        payload.update({
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
        })
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
        *,
        only_reviewed: bool = False,
    ) -> list[Asset]:
        """简单打分检索：每个 keyword 命中 tag/description 得 1 分，高到低排序。

        only_reviewed=True 时只返回已审（meta.needs_review != True）—— 视频生成 agent 用。
        """
        assets = self._catalog[kind]
        if only_reviewed:
            assets = [a for a in assets if not a.meta.get("needs_review")]
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

    def list_by_ip(self, ip: str | None, kind: AssetKind | None = None) -> list[Asset]:
        """按 meta.ip 过滤。ip=None 返回未标 IP 的。

        kind=None 时跨所有类合并。
        """
        kinds = [kind] if kind else list(self._catalog.keys())
        out: list[Asset] = []
        for k in kinds:
            for a in self._catalog[k]:
                a_ip = a.meta.get("ip")
                if ip is None and not a_ip:
                    out.append(a)
                elif a_ip == ip:
                    out.append(a)
        return out

    def list_ips(self) -> list[str]:
        """库中出现过的所有 ip 标记（去重 + 排序）。"""
        seen: set[str] = set()
        for assets in self._catalog.values():
            for a in assets:
                ip = a.meta.get("ip")
                if ip:
                    seen.add(ip)
        return sorted(seen)

    # ---------- 编辑 ----------

    def update(
        self,
        asset_id: str,
        *,
        scene_tags: list[str] | None = None,
        description: str | None = None,
        ip: str | None = None,
        meta_patch: dict | None = None,
        tags: list[str] | None = None,
        save: bool = True,
    ) -> Asset | None:
        """改单条素材的可编辑字段。None = 不动。

        - scene_tags: 写到 meta.scene_tags（最终消费字段）
        - ip: 写到 meta.ip
        - meta_patch: 任意 meta 字段补丁（合并 dict）
        - tags / description: 顶层字段
        """
        a = self.get(asset_id)
        if a is None:
            return None
        if scene_tags is not None:
            a.meta["scene_tags"] = list(scene_tags)
        if description is not None:
            a.description = description
        if ip is not None:
            a.meta["ip"] = ip
        if meta_patch:
            a.meta.update(meta_patch)
        if tags is not None:
            a.tags = list(tags)
        if save:
            self.save()
        return a

    def bulk_update(self, updates: list[dict]) -> dict:
        """批量改。每条 update: {"asset_id": ..., "scene_tags": [...], "ip": ..., ...}.

        返回 {ok_count, missing: [asset_id...]}.
        """
        ok = 0
        missing: list[str] = []
        for u in updates:
            aid = u.get("asset_id")
            if not aid:
                continue
            a = self.update(
                aid,
                scene_tags=u.get("scene_tags"),
                description=u.get("description"),
                ip=u.get("ip"),
                meta_patch=u.get("meta_patch"),
                tags=u.get("tags"),
                save=False,
            )
            if a is None:
                missing.append(aid)
            else:
                ok += 1
        if ok > 0:
            self.save()
        return {"ok_count": ok, "missing": missing}

    def delete(self, asset_id: str, *, save: bool = True) -> bool:
        """删素材登记（不删盘上文件，由 user 决定要不要清理）。返回是否删除成功。"""
        for kind, assets in self._catalog.items():
            before = len(assets)
            self._catalog[kind] = [a for a in assets if a.id != asset_id]
            if len(self._catalog[kind]) < before:
                if save:
                    self.save()
                return True
        return False

    def search_expressions(
        self,
        scene_tags: Iterable[str],
        intensity: int | None = None,
        character: str | None = None,
        *,
        only_reviewed: bool = False,
    ) -> list[Asset]:
        """表情库专用检索。每个 scene_tag 单独建池后 round-robin 合并，确保多 tag 时各自都有曝光。

        - scene_tags: shot_table.SCENE_TAGS 闭集合中的有序列表（顺序代表语义优先级）
        - intensity: 目标强度 (1~3)，None = 不要求
        - character: 限定角色（如"一二布布"），None = 不限
        - only_reviewed: True 时仅返回已人工审核过的（meta.needs_review != True）

        改动原因：之前用 `len(hits)` 单一排序，wanted=[沉思, 兴奋] 但库里没有同时含两者的
        expression，全部 hits=1，sort 稳定 → 目录扫描顺序决定结果，沉思系全被排到后面让用户看不见。
        现在按 tag 分池 + 轮询合并，第一个候选必含第一个 tag、第二个必含第二个 tag。
        """
        wanted = list(scene_tags)
        if not wanted:
            return []
        wanted_set = set(wanted)

        # 每个 tag 单独建池
        pools: dict[str, list[tuple[float, "Asset"]]] = {t: [] for t in wanted}
        for a in self._catalog["expressions"]:
            if only_reviewed and a.meta.get("needs_review"):
                continue
            meta_tags = set(a.meta.get("scene_tags", []))
            hits = wanted_set & meta_tags
            if not hits:
                continue
            if character and a.meta.get("character") != character:
                continue
            # 池内分数：总命中数 + intensity 接近度（同池内排序）
            score = float(len(hits))
            if intensity is not None and "intensity" in a.meta:
                score += max(0.0, 1.0 - 0.4 * abs(intensity - a.meta["intensity"]))
            # 加入它命中的所有 tag 的池
            for t in hits:
                pools[t].append((score, a))
        # 池内按分数降序
        for t in wanted:
            pools[t].sort(key=lambda x: -x[0])

        # Round-robin 合并：第 i 轮各池取第 i 个，去重
        merged: list["Asset"] = []
        seen: set[str] = set()
        max_depth = max((len(p) for p in pools.values()), default=0)
        for i in range(max_depth):
            for t in wanted:
                if i < len(pools[t]):
                    a = pools[t][i][1]
                    if a.id not in seen:
                        merged.append(a)
                        seen.add(a.id)
        return merged

    def approved(self, kind: AssetKind) -> list[Asset]:
        """只返回已审过的素材（视频生成 agent 用）。"""
        return [a for a in self._catalog[kind] if not a.meta.get("needs_review")]

    def approval_stats(self) -> dict:
        """{kind: {total, approved, pending}}."""
        out = {}
        for k, assets in self._catalog.items():
            approved = sum(1 for a in assets if not a.meta.get("needs_review"))
            out[k] = {
                "total": len(assets),
                "approved": approved,
                "pending": len(assets) - approved,
            }
        return out

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
