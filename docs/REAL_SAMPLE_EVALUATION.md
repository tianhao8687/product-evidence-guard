# 真实公开样本与用户体验评测

评测日期：2026-07-30（Asia/Shanghai）  
评测分支：`codex/competition-ready-v1`  
模型：`OpenVINO/Qwen3-VL-8B-Instruct-int4-ov`  
运行时：OpenVINO 2026.2.1 / OpenVINO GenAI 2026.2.1.0  
机器：AMD Ryzen 7 7800X3D、NVIDIA GeForce RTX 5070、Windows

## 结论

项目已经通过“真实公开图片 → 本地模型读取 → 字段归一化 → 冲突分组 →
人工确认 → 正式导出”的实机链路。真实测试不是走 mock，也没有把商品资料发给
云端模型。

当前最有价值的能力是证据可追溯和人工确认安全门；当前最明显的短板是 CPU
首轮推理慢、报告没有可点击的确认/拒绝按钮。以本轮真实样本表现衡量，工程就绪度
暂评 **78/100**，这不是比赛评委分数，也不是统计学准确率。

| 维度 | 分数 | 说明 |
|---|---:|---|
| 证据、审计与安全门 | 18/20 | 候选不自动晋升，来源变化会使旧确认失效 |
| 生产力 Skills 主题贴合 | 18/20 | 把商品资料核验做成可复用、本地、可审计工作流 |
| 稳定性 | 16/20 | 高清图崩溃和模型进程残留已修；仍需扩大压力样本 |
| 真实图提取质量 | 15/20 | 关键数值表现好，仍有一个型号数字识别错误 |
| 速度与小白体验 | 11/20 | 缓存很快，但首次 CPU 推理和命令行确认不够友好 |

## 样本来源与许可

本轮下载了 8 个独立公开样本，并在至少一轮推理中使用了其中 7 个。原图、来源
网页/API 响应和运行结果只留在本机忽略目录，不进入 Git 和发布 ZIP。

| 样本 | 来源 | 许可/用途 |
|---|---|---|
| 电脑电源适配器铭牌 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Etichetta_di_identificazione_adattatore_AC_per_computer_01.jpg) | 按 Commons 文件页记录 |
| GW-A0407D 电源适配器 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Ac_adapter.jpg) | CC BY-SA 3.0 |
| Atari 2600 电源适配器 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Atari-2600-AC-Adapter.jpg) | Public domain |
| 锂聚合物电池 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Lithium_Polymer_Batter.jpg) | CC BY-SA 3.0 |
| Wegmans 8 oz 标签 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Wegmans_Octopus_Salad,_Net_Wt._8_oz._2nd_label_(23418970383).jpg) | Public domain（FDA） |
| Nutella 3017624010701 | [Open Food Facts 图片](https://images.openfoodfacts.org/images/products/301/762/401/0701/front_en.100.400.jpg) | 产品图 CC BY-SA |
| Coca-Cola 5449000000996 | [Open Food Facts 图片](https://images.openfoodfacts.org/images/products/544/900/000/0996/front_en.1107.400.jpg) | 产品图 CC BY-SA |
| Evian 3068320120256 | [Open Food Facts 图片](https://images.openfoodfacts.org/images/products/306/832/012/0256/front_en.61.400.jpg) | 产品图 CC BY-SA |

Open Food Facts 的官方许可说明将数据库标为 ODbL、产品图片标为 CC BY-SA；API
与图片使用方式见其
[API 文档](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/)、
[许可说明](https://openfoodfacts.github.io/openfoodfacts-server/api/tutorials/license-be-on-the-legal-side/)
和[图片下载文档](https://openfoodfacts.github.io/openfoodfacts-server/api/how-to-download-images/)。

本机样本目录：

```text
<project-root>\samples\real\evaluation-20260730
```

本机结果目录：

```text
<artifact-root>\real-image-output-20260730
```

## 最终真实结果

下表的“外层耗时”包含命令启动和本地服务通信，“分析耗时”来自
`run-summary.json`。不同图片内容差异很大，因此不能只用一张图的速度代表全部。

| 场景 | 外层耗时 | 分析耗时 | 结果 |
|---|---:|---:|---|
| 高清电脑适配器铭牌，首次分析 | 160.371 s | 155.377 s | 6 个候选；输入/输出 4 个电气值全部读对 |
| 同一适配器铭牌，缓存复用 | 0.438 s | 0.0203 s | 6 个候选复用；单文件缓存读取 0.0014 s |
| 锂电池标签 | 71.681 s | 71.2662 s | 型号、`1000mAh`、`3.7V` 三项都读出 |
| Nutella 正面图（负样本） | 8.178 s | 7.7034 s | 0 候选；图片上没有可见净含量，因此没有猜 `400g` |
| Wegmans 净含量标签 | 75.573 s | 70.7852 s | `8.0oz` 正确归一为 `226.796185 g` |
| Coca-Cola 正面图（负样本） | 13.681 s | — | 0 候选、无错误 |
| Evian 正面图（负样本） | 14.309 s | — | 0 候选、无错误 |
| 人工确认 | 0.385 s | — | 理由、候选和文件哈希进入审计 |
| 正式导出 | 0.381 s | — | 只导出仍有效的已确认事实 |
| 不支持的 NVIDIA GPU | 0.389 s | — | 明确失败并提示使用 CPU/AUTO，不再“假成功” |
| 安全关闭 | 1.138 s | — | 模型大内存进程已释放 |

两张最终正样本中，共人工核对 9 个候选字符串，8 个与图片可见文字一致。唯一错误
是把 `CPA09-004A` 读成 `CPA009-004A`；因此这组数据只能作为缺陷定位证据，不能
宣传成“88.9% 真实准确率”。适配器的 `100-240V`、`1.5A`、`19V`、`3.16A`
均正确，输入和输出被分开复核，不再互相判为强冲突。

三个 Open Food Facts 商品的数据库记录包含包装数量，但所下载的正面图没有清晰
显示该数量。系统均没有根据商品常识或数据库信息编造图片中不存在的值，这是本轮
负样本测试的正确行为。

## 测试中发现并修复的问题

| 测试发现 | 修复后的行为 |
|---|---|
| 1682×2901 高清图在约 201 s 后触发原生运行时崩溃 | 推理前自动把长边限制到 1024；同一原图稳定完成 |
| `100-240V` 被错误归一成 `-240V` | 保留为范围 `[100, 240] V` |
| 一行同时有电压、电流时可能只映射一个字段 | 增加确定性单位补全，允许一段原文映射多个字段 |
| 型号后缀 `-004A` 被误当成 `4A` | 型号上下文不再生成伪电流候选 |
| 输入与输出电压/电流被判为互相冲突 | 标记 `input` / `output` 语义范围，转为人工复核 |
| 模型 JSON 最后一项被截断时整张图结果丢失 | 只安全保留已经完整闭合的 JSON 项 |
| 复杂图超时后遗留约 11 GB worker，下一次又启动一个 | 精确回收本服务创建的 worker；关闭后不残留大模型进程 |
| NVIDIA GPU 生成失败但顶层看似成功 | 0.4 s 内明确返回错误；这台机器只使用 CPU/AUTO |
| 已确认卡片仍提示“保留为待确认” | 已确认项改为可进入正式导出的文案 |

锂电池样本在修复前用时 123.817 s，只得到 `1000mAh`；修复后用时
71.681 s，同时得到型号、容量和电压。约 42% 的时间下降是本轮探索性对比，前后
服务状态并未做严格实验控制，不能当成标准 Benchmark。

## 用户体验判断

对小白用户而言，当前流程“能用但还不够顺手”：

- 好的部分：输入资料不会被改写；报告会显示原文、来源、候选和风险；缓存复跑、
  确认和导出都在 1 秒内；错误会给出可执行提示。
- 不好的部分：首次 CPU 图片分析约 8–160 秒；复杂图可能更久；HTML 报告是静态
  页面，确认/拒绝仍要复制 `session_id` 和 `candidate_id` 执行命令。
- 这轮未完成桌面浏览器中的点击体验测试。本地浏览器安全策略拒绝直接打开
  `file://` 报告，因此界面结论来自生成 HTML 内容检查和真实 CLI 端到端操作。

模型加载通常约 3.6 秒，不是主要瓶颈；主要耗时在视觉生成。缓存命中后的 0.438
秒说明增量复用设计有效，适合“资料逐步补充、反复复核”的真实工作方式。

## 最优先的下一步

1. 做一个本地可视化审核页：拖入文件、显示阶段进度、直接确认/拒绝、点击回看
   证据区域。这是提升比赛演示和小白体验的最高优先级。
2. 建立至少 50 张、人工标注字段与负样本的真实测试集，固定计算字段 recall、
   数字正确率、单位正确率、幻觉率和 p50/p90；当前 8 个来源样本不够下准确率结论。
3. 增加“铭牌型号、适配器型号、认证编号”等角色区分，避免同一标签上的两个合法
   标识被一概当成强冲突。
4. 尝试先裁剪文字区或传统 OCR 初筛，再把疑难区域交给 Qwen3-VL；同时评估更小
   的视觉模型，降低 CPU 等待时间。
5. 在有 Intel GPU 的机器补做 OpenVINO GPU 数据；本机 RTX 5070 不作为已支持
   设备宣传。

## 自动化回归

该轮修复当时的历史回归：

```text
Ran 134 tests in 53.515s
OK
```

最新最终回归数字以 `SUBMISSION_CHECKLIST.md` 为准。随后 PowerShell 端到端汇总为
`status=passed`，覆盖中文空格路径、编译、确定性
分析、增量复用、确认/拒绝/导出、来源变化后失效、JSON/Markdown/HTML/审计输出、
Named Pipe 状态与安全关闭。
