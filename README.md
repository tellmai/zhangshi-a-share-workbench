# 涨势 · A股主线龙头研究工作台

Windows本地盘后研究软件：采集核验、主线持续性、龙头证据、策略研究、风险预算、人工成交和持仓复盘。现为1.0.0研究版，沿用0.6.0界面和历史记录；数据与严格引擎升级为V2。

## 直接使用

- 本工作区：双击 **启动主线研究台.cmd**，继续使用项目data中的原行情和策略。
- 安装版：release/Zhangshi-Setup-1.0.0-x64.exe。
- 便携版：dist/v1.0.0/Zhangshi/Zhangshi.exe，需整体保留文件夹；发行种子为合成演示数据。
- 本机网页：启动网页版研究台.cmd，地址http://127.0.0.1:8766。

桌面使用WebView2，不启动HTTP监听。网页只监听本机。安装版与便携版的数据位置不同，请在“任务与数据”核对；使用已有目录可指定Zhangshi.exe --data-dir 完整路径。

## 新版工作流

“主线龙头工作台”展示覆盖率、行业/题材分榜、三日持续性、角色、策略证据和严格实验。“持仓与复盘”提供人工/影子/演示账本、费用、现金、T+1、退出计划、CSV预览和追加更正。“任务与数据”提供独立后台进程、检查点恢复、质量、提醒、迁移和备份。

默认研究资金20,000元、单笔风险0.5%、单股25%、总仓50%、最多2只、最长5个交易日。默认值可改；费用和账户未核对时为假设情景。旧策略版本及其资金参数不改变。

原总览、短线复盘、板块、强势股、四模板实验室、指定股票回测和报告功能继续保留。原采样回放与严格历史回测分别标识；不会将今天成分回填为过去已知成分。新基础采集不按当天评分选历史种子。

## 数据与验证边界

旧快照的历史可见时间保持未知。行情缺口、历史证券状态、题材成分、公告与公司行为不足会降级或阻止严格计算；不会编造收益。分钟、竞价、盘口确认和自动委托未启用。

工程发布、真实数据验收、30交易日前瞻观察和策略收益研究分别验收。回填旧记录不能替代真实前瞻积累。程序不连接券商下单。

## 文档

- [最终设计与数据字典](docs/v2-design.md)
- [操作、后台任务、升级及恢复手册](docs/v2-manual.md)
- [详细实施跟踪与后续验收计划](docs/v2-implementation.md)
- [本次验证记录](docs/v2-validation.md)
- [代码完成情况复核](docs/completion-audit.md)
- 既有模块：[桌面](docs/desktop.md)、[策略实验室](docs/quant-lab.md)、[数据源](docs/data-sources.md)、[原快照格式](docs/snapshot-format.md)。这些文档描述旧模块，版本与入口以新版手册为准。

## 源码与检查

使用Python3.12：python -m pip install -r requirements-desktop.txt；可选数据源见requirements-data.txt。requirements-lock.txt记录本次完整运行环境。

Git仓库只包含源码、测试、设计及验收文档。运行数据、原始参考资料、安装包、虚拟环境和构建缓存保留本机；安装包可按构建脚本与installer.iss重新生成。

```powershell
.\.runtime-env\Scripts\python.exe app.py --port 8766
.\.runtime-env\Scripts\python.exe scripts\check.py --node 'Node可执行文件完整路径'
.\.runtime-env\Scripts\python.exe scripts\build_desktop.py
```

首次没有真实行情时显示合成演示。所有数据默认落在data中，源码版本忽略data、运行环境、构建和发行目录。升级前备份保留在build/baseline-0.6.0；旧发行包不删除。新迁移采用摘要去重，不改写旧JSON。
