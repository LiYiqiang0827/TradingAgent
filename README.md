# TradingAgent

A 股金融数据本地化与策略研究系统。

## 模块组成

```
TradingAgent/
├── coreClient/           # 通用 API 客户端(Tushare + 开盘啦 HTTP)
├── offlineDataManager/   # 离线数据下载与本地化(SQLite + 27 个 service)
├── onlineDataManager/    # 在线数据服务(Web / API)
├── policyStudy/          # 政策研究(LLM 分析)
└── docs/                 # 项目文档根目录
```

## 子项目文档

每个子项目都有自己的 README:

- **offlineDataManager**: `~/LLM Wiki/TradingAgent/offlineDataManager/README.md`
- **onlineDataManager**: 子项目内 README
- **policyStudy**: 子项目内 README

## 快速开始

```bash
# 离线下载所有数据(首次约 75 分钟)
/opt/anaconda3/bin/python3 -m scheduler.scheduler_once --only stage0
# 启动常驻调度
/opt/anaconda3/bin/python3 -m scheduler.scheduler_updateData --daemon
```

## 数据规模(2026-09-15 实测)

- **4 个 SQLite 库**(basic / kpl / news / index)
- **31 张表**(含 4 个 ctrl 断点表)+ 27 张数据表
- **34 个 get_* 接口 + 28 个 update_* 方法**
- **27 个 service 子进程**

详细见各子项目 README。

## 隐私说明

本仓库**不包含**:
- 数据库文件(`data/` 目录)
- 运行时日志(`logs/` 目录)
- API token / 密钥

clone 后请根据各子项目文档配置 token。