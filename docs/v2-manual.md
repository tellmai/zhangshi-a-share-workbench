# 涨势1.0.0操作与维护

## 启动与升级

源码工作区双击`启动主线研究台.cmd`，继续使用本项目data中的旧行情、策略与复盘。`启动网页版研究台.cmd`保留本机网页入口。新桌面为进程内服务，不依赖网页服务。

安装版：`release/Zhangshi-Setup-1.0.0-x64.exe`；默认程序位于当前用户LocalAppData/Programs/Zhangshi，用户资料在LocalAppData/Zhangshi/data。升级同一安装位置保留资料。

便携版：整体保留`dist/v1.0.0/Zhangshi`文件夹。直接启动使用旁边data中的合成演示种子；它不会自动复制源码工作区的真实资料。要使用已有资料，在快捷方式或终端中执行：

```powershell
& 'D:\Study\Codex\QuantitativeTrading\dist\v1.0.0\Zhangshi\Zhangshi.exe' --data-dir 'D:\Study\Codex\QuantitativeTrading\data'
```

Windows10/11 64位，需要WebView2。发行包自带Python3.12与依赖。源码运行建议新建Python3.12环境后`python -m pip install -r requirements-desktop.txt`；可选数据源还需`requirements-data.txt`（若使用对应接口）或完整requirements-lock.txt。不要把旧Python3.11的.vendor加入Python3.12。

## 盘后流程

1. 打开“主线龙头工作台”，先看行情日期、有效报价、60根历史及质量说明。当前快照日期不等于电脑今天的日期。
2. 使用“更新基础报价”进入独立任务；失败保留旧快照。使用“独立队列补齐历史”按代码推进，不依据当天排名选择种子。
3. 查看行业/题材分榜、持续性与候选证据。数据不足时只显示已覆盖范围；前三名不足时保留空缺。
4. 在配置中保存资金和费用版本，必要时核对人工账本。预算为零时查看具体原因，不足一手不凑整。
5. “冻结本次研究”保存输入、榜单、证据及版本。后台完成后点“刷新”查看最新产物。
6. 候选可加入共享观察清单。旧短线页面的浏览器清单需在“持仓与复盘”迁入一次，随后自选与清单同步到共同data目录；多窗口修改冲突会提示保留副本。
7. 人工核验公告和实际交易条件。实际发生的成交始终在人工账本记录，可不关联信号。

旧“更新数据”功能和四模板实验室保留原有采样方式和默认资金。新主线工作流优先使用新增基础采集/代码队列。

## 成交与账户

“持仓与复盘”分人工、影子和演示账户。选择正确账户再录入。价格、费用、时间、股数按券商实际回执填写；可选回执编号用于防止重复，人工失效参照和最长持有期形成退出复核计划。

更正会生成新修订；作废需理由且原记录保留。不要用“卖出”抵消错录买入，这会制造未实际发生的交易。出现负现金、无对应买入、T+1冲突或旧估值时先核对，系统不强行平账。成交时点之前的价格不能估值买入后持仓；较早但买入后的有效价格标注为滞后参考。

CSV UTF-8示例：

```csv
code,side,qty,price,executed_at,fees,receipt,note,invalidation_price
600000.SH,buy,100,10.50,2026-11-10T10:00:00+08:00,5.01,receipt-example,示例勿作为真实成交导入,10.00
```

先预览，再确认整批写入。有错误不写入任何一行。买卖方向接受buy/sell及大小写；无回执时使用批次内去重键。相同回执内容不同会阻止整批。初始资金在账户首次记账时冻结，修改新研究资金不会改写旧账户。

“执行偏差与盈亏归属”将人工成交与影子账户按冻结信号、证券、方向和同一执行日核对。可比组展示数量、均价和不利价差；日期不同、信号未知、成交早于信号或缺少影子记录时显示具体状态。不同策略版本和混合买入来源分开展示，归组损益属于关联统计。

汇总成交均价可保留多位小数。账本先按均价乘数量计算成交额，再取到分；部分卖出按FIFO分摊成本和费用，剩余分值留在未卖出数量中。

## 严格研究

新工作台选择明确起止日期、策略、研究假设，按需启用成本压力和滚动验证。系统只读取当时可见的冻结快照，不自动下载今天的数据填补过去。BLOCKED或不完整会保留原因，不输出完整收益。

运行明细可以导出和冻结复现；代码摘要不同会拒绝“精确复现”。历史观察样本的结果仍可在旧实验室读取。指定股票独立账户收益不能相加成组合收益。

影子账户使用当日及时发布的冻结报告，订单保留原信号编号。补做旧日报告不会生成历史影子成交。配置或引擎改变后旧序列停止；可使用新的独立data目录建立新研究账户，原目录保留查询和恢复能力。

前瞻30交易日必须真实积累。补录旧日、演示、合成数据和规则变更前记录不替代该验证。收益曲线不表示策略已获证明。

## 后台与数据维护

“任务与数据”可以查看进度、取消、检查点重试、查看能力、消息和备份。请求发出后任务由独立进程执行；可关闭界面。进程失去心跳120秒后标记中断，可重试。网络等待的取消可能到本次超时才响应。

后台调度默认关闭。“查看计划任务”导出具体命令及时间；“启用后台调度”注册5个当前用户任务。电脑须保持开机、唤醒、用户登录及所需网络。任务执行窗口隐藏；休眠时不强制唤醒。关闭调度只删除本软件在该data目录对应的任务。

请使用“数据与任务”显示的data目录核对桌面/网页是否共用资料。不同data目录相互隔离。迁入旧资料可多次执行，原文件按摘要去重且完整保留。

快照导入和日历导入只用于已核验资料；JSON需明确来源和时间。日历字段：start/end/sessions/source/known_at/verified；不能把普通工作日列表当作交易所日历。

## 命令行与恢复

下列命令使用项目.runtime-env；发行版可以改用`ZhangshiWorker.exe --workbench-worker`后接同样参数。`--data-dir`放在子命令前。

```powershell
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data status
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data migrate --parquet
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data collect --date 2026-11-10
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data backfill --start 2026-01-01 --batch-size 100
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data scan
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data backup
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data schedule
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data verify --backup '备份包绝对路径.zip'
.\.runtime-env\Scripts\python.exe scripts\workbench.py --data-dir data restore --backup '备份包绝对路径.zip' --destination '新的空目录绝对路径'
```

备份不包含桌面缓存、日志和临时文件。恢复后用`Zhangshi.exe --data-dir 新目录`检查原结果，再切换正式快捷方式；恢复工具不会覆盖现有资料。不要在运行中直接复制SQLite主文件代替备份。

数据目录结构：原data文件原位保留；v2/workbench.sqlite3为业务库，v2/objects是冻结JSON，v2/parquet是Parquet，v2/reports为报告，v2/backups为备份，v2/logs为独立任务日志，v2/scheduler保存任务XML。

回滚：使用原0.6.0安装包/便携程序，仍指向原data；旧程序忽略v2目录。新账本和新严格实验只能由1.0.0读取，先导出或保留完整备份。

## 开发验证

```powershell
.\.runtime-env\Scripts\python.exe scripts\check.py --node 'Node可执行文件绝对路径'
.\.runtime-env\Scripts\python.exe scripts\validate_v2.py
.\.runtime-env\Scripts\python.exe scripts\build_desktop.py
```

check只使用隔离测试数据。validate_v2会对实际data执行追加迁移、备份并恢复到build/restore-drill，检查旧JSON摘要与旧引擎结果。它需要build/baseline-0.6.0中的本次升级基线。构建工具发现发行目录中已有运行资料会停止重建，防止覆盖便携用户数据。
