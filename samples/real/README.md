# 真实样例说明

此目录只保留真实样例的来源与校验信息，不把第三方图片打进源码仓库或发布包。

最终真机验证使用的公开领域（Public Domain）样例：

- 文件：`wegmans-octopus-salad-label.jpg`
- 来源：[Wikimedia Commons 文件页](https://commons.wikimedia.org/wiki/File:Wegmans_Octopus_Salad,_Net_Wt._8_oz._2nd_label_(23418970383).jpg)
- 原始尺寸：701 × 1097
- 文件大小：448,698 bytes
- SHA-256：`40c808ce56735a027cc990ee2e476a5b2538c538d1b20e0f6c2647182eabe7cf`
- 复现位置：`<project-root>\samples\real\wegmans-octopus-salad-label.jpg`

下载后应先核对 SHA-256，再通过 `tests/test-real-model.ps1` 执行真实模型验证。
最终离线工件 `<artifact-root>\test-real-model-release-20260730\` 状态
`passed`：CPU、`model_reused=false`、加载 3.6064 s、内层分析 57.1824 s、外层
`analyze` 61.895 s，生成 1 个保持 `pending` 的候选。模型原文为
`NET WT 8.0oz (0.501b)`，映射值为 `8.0oz`，确定性归一为 `226.796185 g`。

这是单次真实图片功能证据，不是准确率结果，也不进入
`benchmark-final-06f8360-20260730` 的 synthetic 准确率统计。
