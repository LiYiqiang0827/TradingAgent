# 每日接入合同：ready_not_enabled

本轮先交手动history/today入口。独立“解除冻结并补齐情绪数据管线”任务仍执行原库更新；待其原库181日F1对账通过且写库互斥路径明确，再由数据管线所有者接入。

触发点是22:00任务实际完成事件；不得按22:01时钟猜测。上游必须释放或交接数据库写锁，并在共用互斥范围内调用today，阻止下一写任务同时启动。weather输出另有 `.mkt.lock` 防重复实例。SQLite只读模式、非空WAL/journal拒绝、读取前后源元信息核对是检测措施，不能代替上游互斥锁。

完成凭证JSON由上游实际结果生成，不手写成功：

```json
{
  "status": "success",
  "trade_date": "YYYY-MM-DD",
  "completed_at": "ISO timestamp",
  "source_state": {
    "basic": {"path": "absolute db_cn_basic.db", "bytes": 0, "mtime_ns": 0},
    "kpl": {"path": "absolute db_cn_kpl.db", "bytes": 0, "mtime_ns": 0},
    "index": {"path": "absolute db_cn_index.db", "bytes": 0, "mtime_ns": 0}
  }
}
```

上面0为格式示例，必须用真实文件状态。`run.source_state(data_root)`提供状态读取。任务失败、日期/状态不符、源在写入时不得发success；入口拒绝并明确提示。原库第一次接入须保留统一验收记录与181日分数/档位对账。节后首个交易日无人干预运行结果另验收，本轮没有把未来事件写成通过。
