# gateway-health：4090历史自愈工具归档

主文档：[gateway-helth命令大全](../../gateway-helth命令大全.md)。

来源为2026-09-09的独立网关自动修复部署。2026-09-17读取4090实际源码后归档，与本地`network_autorepair_20260909/gateway-health.py`统一行尾、去除尾空白后的内容相同。本次不重装、不启用、不改变现网策略。

文件：

- `gateway-health.py`：实际部署的Linux控制器源码。
- `gateway-health.service` / `.timer`：原系统级检查/修复调度单元。
- `test_health.py`：原8项mock隔离测试，临时目录替换状态目录，网络命令被mock；不会操作真实网络。

在Linux使用系统Python、当前目录运行隔离测试：

```bash
python3 test_health.py
```

不要直接运行`gateway-health.py repair`作为测试。控制器需要私有`/etc/gateway-health.json`，其中包括interface、uuid、address、gateway、hub_endpoint、netlogin、accounts、account_name、wg_targets、groups、https_targets；groups还包含unit/table/priority/mark和命名链规格。账号由原登录工具的私有accounts文件读取，密码不在本目录。

2026-09-17发布核验：4090用户临时目录中原8项mock隔离测试全部通过；归档service/timer的systemd-analyze verify退出码0。没有执行实际故障注入、repair或重新部署。

没有提供可一键套用到其他主机的安装器和真实配置。源码沿用旧环境的用户a、Meta、7897和登录service编号等约定；必须先适配受管范围及恢复方案。Windows缺少fcntl，不能直接运行。两个unit只是源文件，归档到仓库不会自动部署。

商业订阅中继的租约、计量、限速及新物理出口策略与这个旧自愈工具分开；本目录不代表那些功能已由gateway-health实现。回滚程序依赖原4090的固定UUID和安装元数据，仅在命令文档说明，不作为通用回滚工具复制。
