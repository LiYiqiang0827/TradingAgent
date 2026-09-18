"""
项目根路径解析 helper。

使用约定:
  ROOT = Path(__file__).resolve().parents[N]

优先从环境变量 TRADE_AGENT_ROOT_PATH 读;没设或路径不存在则 fallback
到脚本位置推算(N 由调用方指定,见 _resolve_root(N) 文档)。

任何脚本应该这样用:

    from _paths import resolve_root
    ROOT = resolve_root(__file__, N=2)   # 或任何需要的层数

或在脚本顶部直接复制本模块里的 _resolve_root() 内联逻辑。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union


def _env_root() -> Union[Path, None]:
    env = os.environ.get("TRADE_AGENT_ROOT_PATH")
    if not env:
        return None
    p = Path(env).expanduser().resolve()
    if not p.exists():
        raise RuntimeError(
            f"TRADE_AGENT_ROOT_PATH={env} 指向的路径不存在;请检查或 unset 走 fallback"
        )
    if not p.is_dir():
        raise RuntimeError(
            f"TRADE_AGENT_ROOT_PATH={env} 不是目录;请检查或 unset 走 fallback"
        )
    return p


def _file_root(script_file: Union[str, Path], n: int) -> Path:
    """脚本位置向上推 n 层。"""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    return Path(script_file).resolve().parents[n]


def resolve_root(script_file: Union[str, Path], n: int) -> Path:
    """
    解析项目根路径。

    Args:
        script_file:  当前脚本文件路径(通常是 __file__)。
        n:            当 TRADE_AGENT_ROOT_PATH 未设置时,从 script_file
                      向上推 n 层得到 fallback 根。

    Returns:
        项目根路径(Path,绝对路径,resolved)。

    Raises:
        RuntimeError: 设了环境变量但路径不存在/不是目录。
    """
    env_root = _env_root()
    if env_root is not None:
        return env_root
    return _file_root(script_file, n)