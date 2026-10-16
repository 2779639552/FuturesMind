# -*- coding: utf-8 -*-
"""bitumen_convert.py — 兼容垫片 (2026-09-18)

真正的实现已泛化为 variety_convert.py(通用「品种数据库」导入器)。
本文件保留旧入口, 行为等价:
    python bitumen_convert.py  ≡  python variety_convert.py --variety bitumen

解析函数与常量(含下划线辅助函数)从 variety_convert 原样转出,
旧脚本/旧测试仍可直接 `import bitumen_convert as bc` 使用。
"""
import variety_convert as _vc

globals().update({k: v for k, v in vars(_vc).items() if not k.startswith("__")})

# 旧默认输出目录(沥青品种目录), 保持向后兼容
OUT_DIR = _vc.DEFAULT_ROOT / "bitumen"


def main():
    _vc.main(default_variety="bitumen")


if __name__ == "__main__":
    main()
