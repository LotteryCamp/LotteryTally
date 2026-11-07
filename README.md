# LotteryTally
福州大学2026软件工程现场编程项目 - 记账本

## 项目结构

```text
LotteryTally/
├── pyproject.toml        # 项目信息、Python 版本、依赖和构建配置
├── lotterytally/         # 应用源码包
│   ├── __init__.py
│   └── filters.py        # 筛选、查询参数和排序
├── test/                 # 自动化测试
└── docs/filters.md       # 筛选模块接口与接入说明
```

## 安装与筛选模块验证

使用 Python 3.10 及以上版本，从仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s test -v
```

业务模块使用 `from lotterytally.filters import ...` 导入筛选接口。
新增依赖统一写入 `pyproject.toml` 的 `project.dependencies`。
当前筛选模块可以独立验证，完整网页启动方式由应用入口模块完成后补充。
