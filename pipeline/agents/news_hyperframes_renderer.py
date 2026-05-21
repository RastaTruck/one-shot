"""Hyperframes 调用层 · 渲染透明背景 chyron 装饰层。

subprocess 调 `npx hyperframes render` 在 hyperframes_studio/ 目录下渲染。
输入: template_id + variables + duration_sec → 输出: ProRes 4444 透明 mov

设计要点：
1. 渲染时长 = audio_dur + 1s buffer，避免 chyron 提前消失
2. data-duration 在 HTML 里写死 30s，渲染前用临时 composition 文件改时长
3. variables 透传到 HTML 的 window.__hyperframes.getVariables()
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time as _time
from pathlib import Path
from typing import Any

_STUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "hyperframes_studio"

# template_id → composition file (相对 _STUDIO_DIR)
# 在这里登记新模板：键是 chyron_styles[].template_id，值是 composition html
TEMPLATE_FILES: dict[str, str] = {
    "funny_zoo":  "index.html",     # V1 · 红蓝白 · GSAP 流光
    "black_gold": "index_v2.html",  # V2 · 黑金财经 · 全程流光
}


def resolve_composition(template_id: str) -> str:
    """template_id → composition 文件名。未知 id 回退到 funny_zoo。"""
    return TEMPLATE_FILES.get(template_id, TEMPLATE_FILES["funny_zoo"])


def is_available() -> tuple[bool, str]:
    """检查 hyperframes 环境是否就绪。"""
    if not _STUDIO_DIR.exists():
        return False, f"hyperframes_studio 目录不存在: {_STUDIO_DIR}"
    if not (_STUDIO_DIR / "package.json").exists():
        return False, "hyperframes_studio/package.json 缺失"
    if not (_STUDIO_DIR / "node_modules" / "hyperframes").exists():
        return False, "hyperframes_studio 未 npm install — 进入目录跑 npm install"
    for tid, fn in TEMPLATE_FILES.items():
        if not (_STUDIO_DIR / fn).exists():
            return False, f"hyperframes_studio/{fn} 模板缺失 (template_id={tid})"
    return True, "ok"


def render_chyron_overlay(
    *,
    variables: dict,
    duration_sec: float,
    out_path: Path,
    fps: int = 30,
    template_id: str = "funny_zoo",
    timeout: int = 300,
) -> dict[str, Any]:
    """渲染透明背景 chyron mov。

    variables: {label, title_main, title_sub, station, time_str}
    duration_sec: 渲染时长（应 ≥ audio_dur 避免 chyron 提前消失）
    out_path: 输出 .mov 路径
    template_id: 模板 ID（见 TEMPLATE_FILES 注册表 · funny_zoo / black_gold）
    """
    ok, msg = is_available()
    if not ok:
        return {"ok": False, "error": msg}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 生成临时 composition 文件：拷贝模板 html + 改 data-duration
    render_dur = max(2, int(round(duration_sec)) + 1)
    src_file = resolve_composition(template_id)
    tmp_comp = _STUDIO_DIR / f".tmp_comp_{int(_time.time() * 1000)}.html"
    try:
        content = (_STUDIO_DIR / src_file).read_text(encoding="utf-8")
        # 替换 data-duration（用正则避免改到其他 duration）
        content = re.sub(
            r'data-duration="\d+(?:\.\d+)?"',
            f'data-duration="{render_dur}"',
            content,
            count=1,
        )
        tmp_comp.write_text(content, encoding="utf-8")

        vars_json = json.dumps(variables, ensure_ascii=False)

        cmd = [
            "npx", "hyperframes", "render", str(_STUDIO_DIR),
            "--composition", tmp_comp.name,
            "--format", "mov",
            "--output", str(out_path),
            "--fps", str(fps),
            "--variables", vars_json,
            "--quiet",
        ]
        t0 = _time.time()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(_STUDIO_DIR),
        )
        elapsed = _time.time() - t0

        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "hyperframes render 失败",
                "stderr": (proc.stderr or "")[-800:],
                "stdout": (proc.stdout or "")[-400:],
            }

        if not out_path.exists():
            return {
                "ok": False,
                "error": "hyperframes 返回成功但输出文件不存在",
                "stdout": (proc.stdout or "")[-400:],
            }

        return {
            "ok": True,
            "out_path": str(out_path),
            "elapsed_sec": round(elapsed, 1),
            "duration_sec": render_dur,
            "size_bytes": out_path.stat().st_size,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"hyperframes 渲染超时 ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            tmp_comp.unlink()
        except Exception:
            pass
