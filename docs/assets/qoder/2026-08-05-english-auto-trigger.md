# Qoder 英文自动触发脱敏记录

日期：2026-08-05

Qoder CLI：1.1.8

## 请求边界

在全新会话中使用英文自然语言请求检查本地商品资料工具是否运行；提示词没有出现
Skill 名称，也没有提供输入目录。请求明确禁止读取商品文件、启动模型和关闭服务。

## 实际结果

- Qoder 自动调用 `Skill` 工具并选择 `local-product-evidence-guard`。
- Skill 参数为 `check local service status only`。
- 随后只执行用户级安装副本的
  `scripts/run.ps1 status`，没有直接调用 Python。
- 工具退出码为 0；稳定 JSON 显示服务已按上一轮测速请求正常关闭。
- 没有读取或分析商品文件，没有启动模型，没有执行 shutdown。
- Qoder 会话成功结束，3 个 agent turns，约 18.8 秒。

这证明英文自然语言可自动路由到已安装 Skill；它不是英文真实业务目录的质量或准确率
评测。
