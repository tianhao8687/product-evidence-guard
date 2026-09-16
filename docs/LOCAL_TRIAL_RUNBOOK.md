# 本地试运行操作

当前是生产化改造后**已通过统一回归的本地试运行版本**，不是已获生产放行的版本；证据与限制见[验收结果](PRODUCTION_VALIDATION_20260916.md)。

1. 保留原件，用单独文件夹放本次商品资料。不要把模型、日志或上次输出混进去。
2. 在本机启动分析。日常只看冲突、待确认、已确认；读取缺口在“查看未读完的资料”中按文件展示。
3. 有冲突就采用正确值；需要改值就点“修改”；没有争议的清晰单来源参数仍可自动确认。
4. 读取缺口优先补充带清晰表头的 CSV/XLSX 或清晰原图，点击“重新读取”。不要反复重试已知不支持的复杂布局。
5. “导出参数表”默认检查完整性。若选择仅导出已确认部分，CSV 与简报会显式标记未完成事项；不能当作完整商品资料交付。

命令行对应操作（在项目环境内运行）：

```powershell
python scripts/client.py export-table --output-dir <输出目录>
python scripts/client.py export-table --output-dir <输出目录> --allow-partial
python scripts/client.py export-local --output-dir <输出目录> --allow-partial
python scripts/client.py cancel --job-id <任务ID>
python scripts/client.py resume --job-id <任务ID>
```

取消只针对本项目该次任务拥有的模型进程，不结束其他 Python / WorkBuddy 工作。已经完成的文件保存在分析检查点；恢复时仍核对来源散列和引擎版本。排队任务取消后不会进入模型。普通 CLI 直接分析应由宿主中断，后台 job 提供可查询的取消状态。

## 安全和失败处理

- 不执行 Office 宏、公式、外部链接或嵌入脚本；公式结果要人工计算核对后另存为仅值副本。隐藏工作表默认不参与核对并明确提示。
- 输入大小、页数、压缩展开量、XML 声明、单元格数、目录条目数受限。复杂或恶意文档可能被拒绝；不要以关闭限制解决问题。
- 新旧原件不能共用失效确认。即便某文件没提取出任何参数，修改它也会影响完整交付检查。
- 报告发布中断时，下次读取恢复上一个完整结果；`.previous-complete-result.json` 保留最近一次发布前的报告副本。分析检查点不是正式交付结果。
- 工作目录及输出含私有资料和记录，仅本机保存；删除整个任务前由用户备份。依赖通过 `requirements.lock` 哈希锁安装。
- 升级前备份程序版本和输出目录；回退使用对应版本的输出备份。不要把不同版本的状态文件手工拼在一起。

## 生产放行

参考 [生产标准](PRODUCTION_READINESS.md)。`scripts/production_acceptance.py` 检查整包原件、金标、预测散列和独立复核记录，逐领域/格式评分；缺少证据返回 blocked。

```powershell
python scripts/production_acceptance.py docs/evidence/production-acceptance-manifest.json --output .runtime/production-acceptance.json
```

空清单返回 blocked 是正确行为，不是测试脚本故障。只有正确率、故障恢复、真实用户和持续运行证据均满足要求，才能决定扩大使用范围。
