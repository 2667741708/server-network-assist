# ERR-NET-009：PowerShell 反复闪窗独立审计

- 日期：2026-09-17。
- 状态：历史周期调用已确认；已知旧调用缺少隐藏控制台；用户确认当前不再出现。
- 影响范围：操作员本机 Windows，已关闭的旧 EXE、现有 Python 后台及新版 GUI。
- 关联需求与验收：REQ-NET-008、TEST-NET-016。

## 触发条件和现象

用户要求独立 check audit 解释反复出现 PowerShell 窗口的具体原因。只读审计期间用户回复“现在已经不再出现”。此次未启停任何客户端、后台、任务、服务，未修改本机网络或重新安装程序。

## 真实证据

主智能体只读读取 Windows PowerShell 事件400最近300条并脱敏保存，不保留完整命令行、密码或订阅令牌：

| 调用类别 | 数量 | 时间范围 +08 | 相邻启动中位间隔 | 实际脚本来源 |
|---|---|---|---|---|
| 物理接入快照 | 153 | 09-16 23:56:47.9 至 09-17 00:07:30.6 | 4.169秒 | 单个 onefile 临时目录 `_MEI419322/server_network_assist/client_attachment_windows.ps1` |
| 界面网络状态 | 144 | 09-16 23:56:50.1 至 09-17 00:12:57.9 | 4.998秒 | ProgramData 的0.7.0后台及多个 onefile 临时目录 |

以上是有限日志样本，不是整小时所有事件计数。00:11—00:12这段仅有网络状态事件，没有再次出现物理接入周期事件。事件400证明引擎启动，不含“窗口是否可见”信息，不能把这些数量直接称为用户看到的窗口数量。临时目录数字不能直接作为父进程PID证据。

30.12秒只读进程/窗口观察未捕获可见PowerShell窗口；捕获3个pwsh均由Codex工具启动且没有可见顶层窗口，旧pythonw后台14984也无可见窗口。轮询可能漏掉很短的进程或窗口，观察时段未持续复现用户此前现象。

证据文件：`artifacts/powershell-start-events-20260917.json`、`artifacts/powershell-flash-observation-20260917.json`；汇总工具：`artifacts/summarize_powershell_start_events.py`。均为只读诊断，不执行网络调整。

## 根因

最有证据支持的历史原因是旧原生 EXE 的物理接入监测：循环调用PowerShell，旧调用未隐藏控制台；快照自身执行时间再加2秒等待，使实际启动间隔约4秒。替换程序文件和桌面快捷方式不会热更新已经运行的旧进程，旧进程仍运行其此前解包和加载的代码，直到正常退出。

独立check_audit直接解包旧EXE `06e4885e1f80c4d6d2dce79c40de77029112650d21f0da98adeec03c9c86c97e`确认：内嵌snapshot没有CREATE_NO_WINDOW，watchdog未借网仍执行，PE Subsystem=3（控制台）。对照已安装新EXE `d6c2d527a106a761dca874c5207bfb7b7e87997b7fa45465ab3c40ff9c703312`：Subsystem=2（GUI），snapshot隐藏，空闲watchdog跳过，补强了上述归因。没有执行两版EXE。

不能将现有Python后台简单视为同一个原因：实际安装的`desktop/0.7.0/packages`中没有`client_attachment.py`，其`desktop.run`有CREATE_NO_WINDOW。当前网络状态刷新约5秒一次，调用链`ClientPanel.state -> desktop.native_status -> desktop.run`也已隐藏控制台。后台PowerShell进程存在和桌面弹窗不是同一事实。

新版确定剩余一个启动时控制台遗漏：[client_paths.py](../../src/server_network_assist/client_paths.py) 的`customer_data()`调用`whoami /user`未设置CREATE_NO_WINDOW。它可能在解析默认配置目录时短闪，但进程是whoami，不是PowerShell，且并非周期性监测，不能解释连续反复的PowerShell窗口。

## 修复方案与实施记录

上一轮安装的D6C2D527A106制品已经包含PowerShell快照、隧道辅助工具、guard/icacls后台隐藏及未借网时跳过持续物理快照。参见[本机GUI更新](2026-09-17-本机接入保护GUI更新.md)。本次用户已确认停止闪窗，因此没有强杀后台或重复安装。

启动时whoami遗漏记录为后续小修项目，应只隐藏进程而不改变身份读取及数据目录语义，不以结束进程或修改网络处理闪窗。

## 验证结果

用户当前明确反馈不再出现；历史事件确认调用节奏与单一物理快照脚本来源；只读窗口观察未复现。上一轮97项隔离测试和新制品静态一致性结果仍有效，本次未修改客户源码或EXE。独立check_audit完成旧/新EXE内嵌代码、PE类型、源码、实际安装后台和事件证据复核，结论与上述历史归因一致；不将用户停止闪窗称为所有场景永久不闪或全部网络兼容通过。

## 风险与回滚

没有此次运行时变更，无需网络回滚。保留旧后台，不结束使用中的实例。再次复现时应记录弹窗时刻、对应进程父链和脚本文件，再判断来源；不根据powershell进程名直接停服务。

## 后续事项

后续版本补充whoami后台隐藏及隔离测试；持续弹窗若再次发生，以实时进程父链和可见窗口采样确认，不用旧后台仍Running作为归因依据。
