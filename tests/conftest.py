"""pytest 配置：加载 .env 环境变量 + 中文显示"""

import pytest
from dotenv import load_dotenv

# 在所有测试模块导入前加载 .env
load_dotenv()


def pytest_collection_modifyitems(config, items):
    """让 pytest 参数化用例直接显示中文，不转义为 \\uXXXX。"""
    for item in items:
        # 强制设置 nodeid 为解码后的中文
        try:
            # 获取原始参数化部分
            if "[" in item.nodeid and "]" in item.nodeid:
                base, param = item.nodeid.rsplit("[", 1)
                param = param.rstrip("]")
                # 尝试解码 Unicode 转义
                decoded = param.encode('utf-8').decode('utf-8')
                item._nodeid = f"{base}[{decoded}]"
        except Exception:
            pass
