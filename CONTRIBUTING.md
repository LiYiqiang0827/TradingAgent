# 加入项目指南(Contributing)

> **目标读者**:拿到 GitHub 邀请的合作伙伴
> **作用**:从 0 → 1 完整加入本项目(clone / 数据准备 / 跑通 / 提交 PR)

---

## 1. 项目地址

- **代码 + 文档仓库**:`git@github.com:LiYiqiang0827/TradingAgent.git`
- **仓库属性**:**Public**(2026-09-18 起公开,任何人都能 clone;敏感数据 / token / 数据库不在仓库内)
- **协议**:**所有 contributor 不得向任何第三方转发敏感资源**(API token、数据库内容、百度网盘链接等不在仓库内的资源)

---

## 2. 加入流程

### 2.1 收到邀请

**项目 owner(`LiYiqiang0827`)会在 GitHub 上邀请你为 collaborator**:
- 你会收到一封 GitHub 邀请邮件
- 接受后你就有 `read + write` 权限(可以 clone + push + 开 PR)

### 2.2 配置 GitHub 账号

1. **注册 GitHub 账号**(如果没有)
2. **添加 SSH 公钥**到 GitHub:
   ```bash
   # 本地生成 SSH key
   ssh-keygen -t ed25519 -C "your_email@example.com"

   # 复制公钥
   cat ~/.ssh/id_ed25519.pub

   # GitHub → Settings → SSH and GPG keys → New SSH key → 粘贴
   ```
3. **测试连接**:
   ```bash
   ssh -T git@github.com
   # 看到 "Hi <username>! You've successfully authenticated..." 就 OK
   ```

### 2.3 配置 Git 身份

```bash
git config --global user.name "Your Name"
git config --global user.email "your_email@example.com"
```

### 2.4 Clone 仓库

```bash
cd ~/   # 或你想要的目录
git clone git@github.com:LiYiqiang0827/TradingAgent.git
cd TradingAgent
```

---

## 3. 环境准备

### 3.1 Python 环境

- **Python 3.11+**(项目用 3.12 / 3.11,旧版本未测)
- 推荐用 `conda` 或 `uv`:
  ```bash
  # conda 方式
  conda create -n trading python=3.12
  conda activate trading
  pip install -r requirements.txt   # 下面会给依赖列表
  ```

### 3.2 系统依赖

- **macOS 12+** 或 **Ubuntu 20.04+**(已测试)
- **`sqlite3`**:系统自带
- **`git`**:系统自带
- **网络**:能访问 `tushare.pro` / `kpl.kaipanla.com` / `ths.com`

### 3.3 Python 依赖

```text
pandas >= 1.5
numpy >= 1.20
loguru >= 0.6
tushare >= 1.4
redis >= 4.0
requests >= 2.28
pytdx >= 1.72
```

---

## 4. 数据准备(⚠️ 必做)

**⚠️ 仓库是 private 但 GitHub 100 MB 文件大小限制** — **21 GB 的数据库不在 GitHub 上**。需要单独从**百度网盘**下载。

详见 → **[`数据库使用须知.md`](./数据库使用须知.md)**

简要:
1. 联系项目 owner(`LiYiqiang0827`)拿百度网盘分享链接
2. 下载 6 个 `.db` 文件(总 28GB)
3. 按指定路径放置(下面有目录表)
4. 验证 db 完整性和 schema

---

## 5. 跑通验证(冒烟测试)

```bash
cd ~/TradingAgent

# 1. 测试 import
PYTHONPATH=. python3 -c "
from coreClient.data_provider import get_basic
df = get_basic(exchange='SSE', market='主板', list_status='L')
print(f'✅ get_basic 返回 {len(df)} 行')
"

# 2. 测试 db 模式(应该有数据)
PYTHONPATH=. python3 -c "
from coreClient.data_provider import get_kpl_limit_performance
df = get_kpl_limit_performance(trade_date='2026-09-17', source='database')
print(f'✅ get_kpl_limit_performance 返回 {len(df)} 行')
"

# 3. 测试 online 模式(需要配置 token,见下)
PYTHONPATH=. python3 -c "
from coreClient.data_provider import get_basic
df = get_basic(source='online')
print(f'✅ get_basic online 返回 {len(df)} 行')
"
```

### 5.1 配置文件

```bash
mkdir -p ~/.trading-agent
# 复制 config 模板
cp offlineDataManager/scripts/config/secret.example.py ~/.trading-agent/secrets.py
# 编辑填入你的 token
nano ~/.trading-agent/secrets.py
```

需要的 token:
- **TUSHARE_TOKEN**(tushare.pro 申请)
- **KPL_AUTH_TOKEN**(开盘啦,找 owner 拿)
- **THS_ACCOUNT**(同花顺账号,可选)

---

## 6. 必读文档(按优先级)

1. ⭐⭐⭐ [`docs/AI_AGENT_START.md`](./docs/AI_AGENT_START.md) — 5 分钟项目速读
2. ⭐⭐ [`README.md`](./README.md) — 入口
3. ⭐⭐ [`docs/TradingAgent项目概览.md`](./docs/TradingAgent项目概览.md) — 项目全景
4. ⭐ [`coreClient/docs/data_provider.md`](./coreClient/docs/data_provider.md) — 30 个数据接口
5. ⭐ [`policyStudy/docs/ARCHITECTURE.md`](./policyStudy/docs/ARCHITECTURE.md) — 策略架构

---

## 7. 提交规范

### 7.1 Commit message 格式

参考已有 commit:
```
<type>: <subject>

<body>

<footer>
```

**Type**:
- `feat` — 新功能
- `fix` — 修 bug
- `refactor` — 重构(无功能变化)
- `docs` — 文档
- `chore` — 构建 / 工具 / 杂项
- `test` — 测试
- `style` — 代码格式

**示例**:
```
feat: 加 get_block_trade online 多日支持

- tushare.block_trade 不支持多日,自动拆日循环
- 加 ≤ 30 交易日限制
- 92 个新测试断言
```

### 7.2 PR 流程

1. **不直接 push main** — 创建 feature 分支:
   ```bash
   git checkout -b feat/my-feature
   ```
2. **commit + push 分支**:
   ```bash
   git add .
   git commit -m "feat: ..."
   git push origin feat/my-feature
   ```
3. **GitHub 上开 PR**:`feat/my-feature` → `main`
4. **等 owner review + merge**

### 7.3 文档同步

**本项目只有 1 个 git 仓库**(代码 + 文档一起):
- 修改代码 → 同时更新对应 `docs/` 文档
- PR 描述里写明改了哪些文件

---

## 8. 隐私 / 保密(⚠️ 必读)

### 8.1 严禁转发

- **代码**(商业项目)
- **数据库内容**(有商业价值的数据快照)
- **API token / 密钥**(Tushare / KPL / THS)
- **百度网盘分享链接**(其他人无访问权限)

### 8.2 严禁入库

`.gitignore` 已排除:
- `**/data/` — 数据库
- `**/logs/` — 日志
- `**/watchlist/*.csv` — 等等(`**/watchlist/*.csv` 实际有,看 `.gitignore`)
- `*.env` / `*.key` / `*token*` / `*secret*` — 密钥

**如果发现 token 误提交**:**立刻** 联系 owner 改 token + 用 `git filter-branch` 清理历史。

### 8.3 提交前自检

```bash
# 提交前扫一下有没有 token
git diff --staged | grep -E 'token|secret|password' -i
```

---

## 9. 常见问题

**Q: 我没收到 GitHub 邀请怎么办?**
A: 联系 owner(`LiYiqiang0827`),他会重新发邀请。

**Q: 数据库太大下载慢怎么办?**
A: 先下载 `db_cn_index.db`(13MB,小),够跑 demo。大的几个按需下载。

**Q: 我能修改项目架构吗?**
A: 大改动先开 issue 讨论。小改动直接 PR。

**Q: 报错 `ModuleNotFoundError: No module named 'coreClient'`?**
A: 在 `~/TradingAgent/` 目录下跑命令,且设 `export PYTHONPATH=~/TradingAgent`。

**Q: 我能用 Windows 吗?**
A: 项目没在 Windows 上测过。理论上可以,但 `pytdx` 在 Windows 上需要额外配置。

---

## 10. 联系人

- **项目 owner**:`LiYiqiang0827`
- **Issues**:在 GitHub 上提 issue(私有仓库仅 collaborator 可见)

---

## 变更历史

| 日期 | 变更 |
|---|---|
| 2026-09-18 | v1.0 初版 |