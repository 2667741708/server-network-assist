"""Package only the tested customer EXE and public documentation, never user data."""
import hashlib
import argparse
import json
from pathlib import Path
import zipfile
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--artifact', type=Path, default=root / 'artifacts/client-url-code-20260917')
parser.add_argument('--expected-hash', default='b40e189839e3cc2c4cce4936ca8203e2183b7fe614a7113b4e07a7aae1c90c6d')
parser.add_argument('--revision', default='20260917-url-and-code')
parser.add_argument('--zip-name', default='纯享入网-Windows-20260917.zip')
parser.add_argument('--installer', action='store_true', help='Include built Install.exe')
parser.add_argument('--offline-dependencies',action='store_true',help='Include official offline prerequisite installers')
args = parser.parse_args()
artifact = args.artifact.resolve()
exe = artifact / 'ServerNetworkAssistClient.exe'
expected = args.expected_hash.lower()
payload = exe.read_bytes()
if hashlib.sha256(payload).hexdigest() != expected:
    raise ValueError('Unexpected customer EXE; distribution stopped')
instructions = '''# 纯享入网 Windows 客户端

这是订阅入网客户端，不是服务器管理端，也不是纯享入网 Next。
revision：20260917-url-and-code。无需安装 Python，无需填写校园网账号密码。
当前为免 Python 的 EXE 分发包，不是自动安装所有网络组件的一键安装器。

## 首次使用

1. 将整个 ZIP 解压到固定文件夹，不要直接在压缩包内运行。
2. 安装 WireGuard Windows 官方客户端： https://www.wireguard.com/install/ 。本程序调用 WireGuard 创建和清理自己的客户隧道；不需要手动填写 WireGuard 配置。
3. 本程序窗口使用 Microsoft WebView2 Runtime；若电脑缺少它或提示运行库缺失，从 https://developer.microsoft.com/en-us/microsoft-edge/webview2/ 安装 Evergreen Runtime。
4. 双击 ServerNetworkAssistClient.exe，名称为“纯享入网”；允许管理员授权，才能创建系统隧道。
5. 在“添加订阅”粘贴管理员单独提供给这台电脑的完整订阅网址或 PURE1- 开头的订阅码，点击“添加订阅”。不需要打开订阅网页，不会自动入网。
6. 主页面“我的订阅”显示已保存身份，可以切换或移除；选择源网节点，再点击“开始入网”。
7. 启动入网前需连接能实际访问源网的校园 Wi-Fi 或有线局域网。Wi-Fi 名称含 iYanDa 并不保证无线到源网互通。当前服务是校园内网服务，校外/热点无法访问时应保留原网络并等待校园路径可达，不要反复关闭自己的代理或修改路由来强行连接。
8. 退出时点击“退出入网 · 恢复原网络”。关闭窗口也执行退网，最小化继续在通知区运行。切换订阅先退网，切换后需手动开始入网。

## 订阅和个人配置

- 软件包不带任何订阅：必须向管理员领取单独的客户订阅。不要把 D321 或其他电脑已经兑换的开户链接转发给新电脑。
- 订阅码内封装地址和一次性令牌，是编码不是加密；与完整订阅网址同样需要保密。
- 客户端只控制本机入网/退网，不提供源网主机 SSH、远程桌面或商业管理密钥。
- 管理员为订阅选择源网、期限、额度、限速和物理/源网代理权限；软件本身不附带机场订阅，也不自动给个人 Clash 更换订阅。
- 两种输入格式使用同一套授权；更换格式不会续期、解锁设备数或提高额度。
- Windows 数据按用户身份单独保存；升级前先退网并关闭旧窗口。只换 EXE 不会把已运行进程热更新。

## 常见问题

- 无法连接服务端：检查管理员给出的地址、校园 Wi-Fi 到有线网络互通、服务监听、防火墙及其他 VPN/TUN。能连接 Wi-Fi 或读到订阅不等于已成功入网；需要源网握手成功。
- 开始入网不可用：先查看订阅、选择可用源网并检查校园接入。不要填写自己的校园网账号来替代订阅。
- 提示已启动：使用当前窗口/通知区图标；升级时先退网退出旧程序，再打开新 EXE。
- 本包不包含 WireGuard 和 WebView2 安装器；有上述组件后，可通过 EXE 使用，不需要开发环境。
- 本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。

## 联系管理员时

提供错误文字、客户端 revision、连接的是校园有线/Wi-Fi/热点即可。不要发送设备私钥、客户数据目录或完整订阅码到公共群。
'''
instructions = instructions.replace('20260917-url-and-code', args.revision)
if args.revision == '20260918-auto-source-status':
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版通过隔离浏览器自动源网查询、Python接入与退网回归、冻结源码及资源一致性核验；本轮未启动操作员本机客户端、未部署D321或执行真实入网/TUN验收。')
    instructions += '\n## 自动源网状态与接入范围\n\n启动后读取已保存订阅；添加或切换订阅后自动查询源网，后台约30秒刷新。节点显示物理/源网代理出口、TCP服务可达性和连接耗时；TCP可达不代表UDP隧道或公网代理一定可用。查询不会自动入网、切换Wi-Fi、修改路由/DNS/代理或注销个人账号。\n\n不再用固定IPv4网段名单拒绝接入；任意IPv4前缀均可验证当前物理网络到源网的实际路径。实际入网仍验证订阅授权、校园认证和WireGuard握手；当前数据隧道只支持IPv4，有IPv6默认出口时入网保护仍阻止接管，但不阻止订阅/源网状态查询。校外无法访问校园源网属于网络路径不可达，不是客户端IP黑名单。\n'
if args.revision == '20260919-subscription-egress-labels':
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版通过订阅历史与出口标签回归、冻结源码核验和完整离线包校验；D321 实机重装及物理/源机代理两类订阅的入网退网结果见部署验收记录。')
    instructions += '\n## 订阅出口标签\n\n“我的订阅”和当前订阅会明确显示“物理出口订阅”或“源机代理出口订阅”。首次读取订阅后保存公开的出口类型，切换订阅或服务暂时离线时仍可辨认；界面不会展示设备私钥或一次性令牌。物理出口使用源机物理宽带，外网代理由客户自己的 Clash 决定；源机代理出口由源网服务器的代理路径转发。\n'
if args.revision == '20260918-smooth-navigation':
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版通过隔离浏览器导航/操作回归、34项Python测试、冻结源码与UI资源核验；本轮没有运行操作员本机客户端或真实入网/TUN测试。')
    instructions += '\n## 丝滑导航\n\n订阅历史、接入帮助及通知设置使用Framework7现成折叠组件，保留输入与选中状态；支持浏览器前进/后退和减少动画。添加订阅、开始/退出入网仍是明确操作，不会由悬停预执行。\n'
if args.revision == '20260917-sol-ui':
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版由 gpt-5.6-sol 优化普通客户端，复用本地 Framework7 iOS 组件；通过隔离浏览器、相关 Python 回归和 EXE 静态资源/源码一致性检查。本轮没有运行真实入网、修改本机网络或部署 D321，不以模拟预览代替实机校园接入验收。')
    instructions += '\n## 界面布局\n\n添加订阅始终置顶；当前订阅、节点和开始/退出相邻。套餐额度与本机统计独立展示。历史订阅、接入帮助和通知设置可折叠；不可用节点不能选择。\n'
if args.revision == '20260917-subscription-top':
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版将添加订阅放到主页面最上方，默认展开；隔离浏览器验证通过。上一版已在 D321 Windows 完成 GUI 升级和历史导入，本版尚未更新 D321 或重新执行真实入网、TUN 和新电脑环境验收。')
if args.revision == '20260917-subscription-fold':
    instructions = instructions.replace('主页面“我的订阅”显示已保存身份，可以切换或移除', '主页面“我的订阅”默认折叠并显示数量，点击标题或箭头展开后可查看、切换或移除已保存身份')
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版将添加订阅放在顶部、订阅列表改为可折叠；隔离浏览器验证通过，在 D321 Windows 完成真实 GUI 部署和未入网时路由/DNS/代理比对。本轮未重新执行所有真实入网、TUN 和新电脑环境验收。')
if args.installer:
    instructions = instructions.replace('当前为免 Python 的 EXE 分发包，不是自动安装所有网络组件的一键安装器。', '本包提供 Install.exe 安装向导；只安装客户端文件，不启动入网、不设置开机启动、不修改网络。')
    instructions = instructions.replace('将整个 ZIP 解压到固定文件夹，不要直接在压缩包内运行。', '完整解压 ZIP，双击 Install.exe。选择安装目录；如需要桌面快捷方式，勾选“允许创建桌面快捷方式”。默认不勾选。完成后从快捷方式或提示的安装路径打开客户端。不要在 ZIP 内直接运行。')
    instructions = instructions.replace('双击 ServerNetworkAssistClient.exe，名称为“纯享入网”；允许管理员授权，才能创建系统隧道。', '打开已安装的“纯享入网”；允许客户端管理员授权，才能创建系统隧道。安装向导本身按当前用户安装，无需管理员权限。')
    instructions = instructions.replace('主页面“我的订阅”显示已保存身份，可以切换或移除', '主页面“我的订阅”默认折叠并显示数量，展开后可查看、切换或移除已保存身份')
    instructions = instructions.replace('本次真实验证在 D321 Windows 上完成了 GUI 升级、历史导入及未入网时路由/DNS/代理不变；本轮未重新执行所有真实入网、TUN 和新电脑环境验收。', '本版通过隔离的安装文件操作、流量计数恢复及浏览器 GUI 验证；尚未在全新 Windows 客户电脑执行安装向导和真实入网验收。')
    instructions += '''
## 安装配置与流量记录

- 安装文件放在所选目录的 app-<文件摘要> 子目录；升级保留旧程序，不强行结束运行中的客户端。
- 订阅、偏好和统计放在 %LOCALAPPDATA%\\PureNetworkClient\\data，配置目录限制当前用户、SYSTEM 和管理员访问。client-install.json 只保存此目录的位置，没有订阅密钥。
- customer-online-subscriptions.json 保存订阅列表及设备身份；client-traffic.json 保存各订阅的本机流量统计。不要共享或手动编辑这些私有文件。
- 已使用旧便携版的客户可以在“我的订阅”中点击“导入旧版订阅”；向导不会自动搬走或删除旧数据。升级安装版沿用相同配置目录。
- “今日流量”按电脑本地日期累计上行加下行；“此订阅累计”为本机测量开始以来的累计，不补算旧版历史、不代表其他电脑用量。
- 实时速度读取此客户端拥有的 WireGuard 隧道，约 2 秒采样、界面 5 秒刷新；不是套餐限速。停止入网显示零，无法取得计数时显示未知。
- 跨午夜的单个采样间隔归入采样当天。统计不包含校园内网直连和绕开客户隧道的流量；套餐剩余额度及扣费以服务端为准。
- 正常退出前尽力保存最后一次计数；意外强制退出可能遗漏尚未采样的少量流量。统计读写失败不阻止退网与原网络恢复。
'''
entries = {
    'ServerNetworkAssistClient.exe': payload,
    '使用说明.md': instructions.encode('utf-8-sig'),
    'LICENSE.txt': (root / 'LICENSE').read_bytes(),
    'THIRD_PARTY_NOTICES.md': (root / 'THIRD_PARTY_NOTICES.md').read_bytes(),
    'licenses/Framework7-LICENSE.txt': (root / 'src/server_network_assist/desktop_ui/FRAMEWORK7-LICENSE.txt').read_bytes(),
}
if args.installer:
    entries['Install.exe'] = (artifact / 'Install.exe').read_bytes()
    import sys
    runtime = Path(sys.base_prefix)
    python_license = next(p for p in (runtime / 'LICENSE.txt', runtime / 'LICENSE_PYTHON.txt') if p.is_file())
    entries['licenses/Python-LICENSE.txt'] = python_license.read_bytes()
    entries['licenses/Tk-license.terms'] = (runtime / 'Library/lib/tk8.6/license.terms').read_bytes()
if args.offline_dependencies:
    if not args.installer:
        raise ValueError('Offline prerequisites require Install.exe')
    from server_network_assist.client_dependencies import OFFLINE_FILES
    for name in OFFLINE_FILES:
        entries[name]=(artifact/name).read_bytes()
    upstream=artifact/'wireguard-windows-v1.1-source.zip'
    with zipfile.ZipFile(upstream) as source_archive:
        assert source_archive.testzip() is None
        entries['licenses/WireGuard-COPYING.txt']=source_archive.read('wireguard-windows-1.1/COPYING')
    entries['licenses/wireguard-windows-v1.1-source.zip']=upstream.read_bytes()
    text=entries['使用说明.md'].decode('utf-8-sig')
    text=text.replace('2. 安装 WireGuard Windows 官方客户端： https://www.wireguard.com/install/ 。本程序调用 WireGuard 创建和清理自己的客户隧道；不需要手动填写 WireGuard 配置。','2. 在安装窗口点击“一键安装”。安装器检测 WireGuard 和 WebView2，缺少时从包内安装官方组件；允许 Windows 管理员授权。无需先登录校园公网、联网下载或手动配置 WireGuard。')
    text=text.replace('3. 本程序窗口使用 Microsoft WebView2 Runtime；若电脑缺少它或提示运行库缺失，从 https://developer.microsoft.com/en-us/microsoft-edge/webview2/ 安装 Evergreen Runtime。','3. 等待安装完成。若系统组件要求重启，先保存工作、手动重启，再运行 Install.exe 完成复查；安装器不会自动重启电脑。')
    text=text.replace('本包不包含 WireGuard 和 WebView2 安装器；有上述组件后，可通过 EXE 使用，不需要开发环境。','本包包含官方 WireGuard x64 MSI、WebView2 x64 Evergreen完整离线安装器。Python、pythonnet、Tk、客户端库和本地UI资源已冻结在EXE中；Windows 10/11 x64内置 .NET Framework 4.x 与标准系统工具。不是x86/ARM64安装包。')
    text=text.replace('本包提供 Install.exe 安装向导；只安装客户端文件，不启动入网、不设置开机启动、不修改网络。','本包提供 Install.exe 离线安装向导，安装客户端文件与缺失系统依赖；不创建客户隧道、不启动入网、不设置开机入网，不改变现有代理/DNS/路由。官方WireGuard安装会安装其管理服务和驱动。')
    text=text.replace('安装向导本身按当前用户安装，无需管理员权限。','客户端文件按当前用户安装；安装缺失系统组件时需要管理员授权。')
    text=text.replace('订阅、偏好和统计放在 %LOCALAPPDATA%\\PureNetworkClient\\data，','新用户的订阅、偏好和统计默认放在 %LOCALAPPDATA%\\PureNetworkClient\\data，')
    text=text.replace('向导不会自动搬走或删除旧数据。升级安装版沿用相同配置目录。','若当前用户的标准便携版配置已存在，向导优先沿用该目录并在窗口显示；已有安装版配置优先。不会搬走或删除旧数据；其他自定义目录仍可显式导入。')
    text=text.replace('本版通过隔离的安装文件操作、流量计数恢复及浏览器 GUI 验证；尚未在全新 Windows 客户电脑执行安装向导和真实入网验收。','本版通过缺失组件前置检查、安装文件及相关客户端回归、EXE冻结源码与资源核验、完整ZIP校验和安装器无安装自检。本轮未在全新Windows电脑执行真实系统组件安装和校园入网测试。')
    text+='\n## 缺少组件提示\n\nWinError 2仅表示程序文件找不到，不能凭错误编号确定文件。新版开始入网前检查WireGuard文件，缺失时提示退出客户端运行Install.exe，既有订阅保持。全部解压后再安装，不单独复制Install.exe或客户端。安装包无订阅，无需重新兑换已经保存的订阅。\n'
    entries['使用说明.md']=text.encode('utf-8-sig')
    entries['依赖清单.json']=json.dumps({'platform':'Windows 10/11 x64','offline_files':list(OFFLINE_FILES),'python_in_exe':True,'dotnet':'Windows内置.NET Framework 4.x','wireguard_source':'https://download.wireguard.com/windows-client/','webview2_source':'https://developer.microsoft.com/en-us/microsoft-edge/webview2/','installation_starts_client':False,'contains_subscriptions':False},ensure_ascii=False,indent=2).encode('utf-8')
# Preserve packaged runtime dependencies' license files; no credentials or config.
site = root / 'artifacts/desktop-build-env/Lib/site-packages'
for dist in sorted(site.glob('*.dist-info')):
    for path in sorted(dist.rglob('*')):
        if path.is_file() and any(part.lower().startswith(('license', 'copying', 'notice', 'authors')) for part in path.relative_to(dist).parts):
            entries['licenses/' + dist.name + '/' + path.relative_to(dist).as_posix()] = path.read_bytes()
base_manifest = {'product': '纯享入网', 'revision': args.revision,
                 'contains_subscriptions': False, 'contains_user_data': False,
                 'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}}
entries['文件校验.json'] = json.dumps(base_manifest, ensure_ascii=False, indent=2).encode('utf-8')
if Path(args.zip_name).name != args.zip_name or not args.zip_name.endswith('.zip'):
    raise ValueError('Invalid ZIP name')
destination = artifact / args.zip_name
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name, data in entries.items():
        archive.writestr('纯享入网/' + name, data)
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert set(archive.namelist()) == {'纯享入网/' + name for name in entries}
    assert hashlib.sha256(archive.read('纯享入网/ServerNetworkAssistClient.exe')).hexdigest() == expected
report = {'zip': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
          'bytes': destination.stat().st_size, 'entries': len(entries), 'exe_verified': True,
          'user_data_included': False, 'network_operations': False}
(artifact / 'distribution-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
