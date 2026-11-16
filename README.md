# 跨市场设备服务质量数据平台

该 FastAPI 服务在离线环境下分析不同市场的设备运维事件。演示数据均为本地合成数据，SQLite 是唯一持久化服务。

## 启动

依赖精确版本见 requirements.txt。在预装依赖的环境中运行 uvicorn brand_signal.api:app --host 0.0.0.0 --port 8000。通过 BRAND_SIGNAL_DB 指定数据库文件，默认是 /tmp/brand-signal.sqlite3。运行 python -m brand_signal.demo 可执行一次性端到端演示。

## 数据流程

先登记市场及 IANA 时区、产品和设备安装记录。可选配置来源字段契约、外部设备号映射和服务响应目标。工单及计量事件使用请求键批量提交；每行分别返回接收或拒绝结果，相同请求键和内容重放原结果。工单变更需要预期修订号。按市场本地日期查询分析指标、跨来源对账、告警及服务复核。

发布候选固定某一源修订及预期发布版本。只有源修订和发布版本未变化时才能发布。旧版本、CSV 和 JSON 导出不可变。迟到数据须进入新候选和新发布版本。市场汇总、比较、抽样与溯源均按明确版本读取。

## 服务商责任与转派

海外工单在当地服务商之间转派。先登记服务商，再登记带半开生效区间 `[valid_from, valid_to)` 的责任关系：`product` 为空表示市场级默认责任，产品级责任优先；同一范围的区间禁止重叠（端点相接允许），责任区间可按修订号关闭后变更，并发修订以 `stale_responsibility` 拒绝。

工单在打开时刻按市场、产品解析初始责任方，转派记录携带事件时间，责任链按事件时间重放成互不重叠的持有片段，任意时刻只有一个责任方。转派推进工单修订号，交互转派用 `expected_revision` 做乐观锁（`stale_ticket`），批量渠道走请求键幂等并逐行返回接收/拒绝原因。重复转派（同工单同事件时间 `duplicate_reassignment`）、自转（`self_reassignment`）、转出方与当时持有方不符（`from_provider_mismatch`）、目标方无有效责任区间（`responsibility_gap`）、对已关闭/取消工单转派（`reassignment_window_closed`、`ticket_not_active`）都有稳定错误码。

责任分析按市场、产品和责任方统计响应、结案、积压、持有时长与转派出入：响应归 ack 时刻持有方、结案归 close 时刻持有方、积压归日末持有方，持有时长按自然日裁剪，跨责任边界的工单不会被重复计入；无责任覆盖的工单归入 `provider=null` 桶。责任分析随发布候选冻结到 `publication_accountability`，服务商变更或迟到转派会推高源修订、使在途候选失效，已发布版本保持不变，下一发布版本才反映归属变动。

## 验证

python -m unittest discover -s tests -v

python -m compileall -q brand_signal

python -m brand_signal.demo

API 错误返回稳定的 error 代码和 HTTP 4xx 状态。空分母表示为 JSON null。输入时间戳必须包含时区偏移，本地日期格式为 YYYY-MM-DD。
