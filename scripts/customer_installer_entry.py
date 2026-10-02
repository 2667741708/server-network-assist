"""Native Windows installer using standard Tk controls. Never launch the client."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
import queue
import threading
from tkinter import filedialog, messagebox, ttk

from server_network_assist.client_install import install_files, choose_data_directory
from server_network_assist.client_paths import customer_data
from server_network_assist.client_dependencies import status, verify_offline_payload


def main():
    bundle = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    resource = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    helper = resource / 'customer_install_windows.ps1'
    dependency_helper = resource / 'customer_dependencies_windows.ps1'
    if '--self-check' in sys.argv:
        # No window, install, shortcuts, services, or network. Validate frozen Tk
        # resources and payload before distributing the installer.
        import hashlib
        report = Path(sys.argv[sys.argv.index('--report') + 1])
        manifest = json.loads((bundle / '文件校验.json').read_text(encoding='utf-8'))
        for name, digest in manifest['files'].items():
            target = (bundle / name).resolve()
            if not target.is_relative_to(bundle.resolve()) or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Bundle verification failed')
        interpreter = tk.Tcl()
        result = {'tcl_version': interpreter.call('info', 'patchlevel'),
                  'tk_dll_present': (resource / 'tk86t.dll').exists(),
                  'helper_present': helper.is_file(), 'bundle_verified': True,
                  'network_operations': False, 'installation_performed': False,
                  'dependency_helper_present': dependency_helper.is_file(),
                  'offline_dependencies_verified': verify_offline_payload(bundle)}
        if not result['tk_dll_present'] or not result['helper_present'] or not result['dependency_helper_present']:
            raise ValueError('Installer runtime incomplete')
        report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        return
    local = Path(os.environ['LOCALAPPDATA']) / 'PureNetworkClient'
    data = choose_data_directory(local/'data',customer_data())
    window = tk.Tk()
    window.title('纯享入网 · 安装')
    window.geometry('640x540')
    window.minsize(560, 520)
    style = ttk.Style(window)
    if 'vista' in style.theme_names():
        style.theme_use('vista')
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='安装纯享入网', font=('Microsoft YaHei UI', 17)).pack(anchor='w')
    ttk.Label(frame, text='离线安装客户端及缺失的 WireGuard / WebView2。系统组件需要管理员授权。\n安装完成后不会自动入网，也不会设置开机入网。',
              wraplength=570).pack(anchor='w', pady=(12, 20))
    path = tk.StringVar(value=str(Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'PureNetworkClient'))
    row = ttk.Frame(frame)
    row.pack(fill='x')
    entry = ttk.Entry(row, textvariable=path)
    entry.pack(side='left', fill='x', expand=True)
    def browse():
        chosen = filedialog.askdirectory(title='选择安装目录', parent=window)
        if chosen:
            path.set(str(Path(chosen) / 'PureNetworkClient'))
    browse_button=ttk.Button(row,text='选择目录…',command=browse)
    browse_button.pack(side='right',padx=(8,0))
    desktop = tk.BooleanVar(value=False)
    desktop_button=ttk.Checkbutton(frame,text='允许创建桌面快捷方式“纯享入网”',variable=desktop)
    desktop_button.pack(anchor='w',pady=18)
    ttk.Label(frame, text='订阅和流量配置按当前 Windows 用户保存，升级时保留；不随分发包分享。\n配置目录：' + str(data),
              wraplength=570).pack(anchor='w')
    def dependency_text():
        values = status()
        return '组件检查：WireGuard ' + ('已安装' if values['wireguard'] else '待安装') + ' · WebView2 ' + ('已安装' if values['webview2'] else '待安装')
    progress_text = tk.StringVar(value=dependency_text())
    ttk.Label(frame, textvariable=progress_text, wraplength=570).pack(anchor='w', pady=12)
    progress = ttk.Progressbar(frame, mode='indeterminate')
    progress.pack(fill='x')
    def helper_run(action, *args):
        power_shell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        subprocess.run([power_shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                        '-File', str(helper), '-Action', action, '-DataPath', str(data), *args],
                       capture_output=True, check=True, timeout=30, creationflags=0x08000000)
    results = queue.Queue()
    installing = False
    def install():
        nonlocal installing
        if installing:
            return
        if not path.get().strip() or not Path(path.get()).is_absolute():
            messagebox.showerror('安装未完成', '请选择完整的安装目录。', parent=window)
            return
        destination, make_desktop = path.get(), desktop.get()
        def work():
          instance = data / 'client-instance.json'
          try:
            if instance.exists():
                pid = int(json.loads(instance.read_text(encoding='utf-8'))['pid'])
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    raise ValueError('请先退出入网并关闭已运行的客户端，再升级安装。')
            verify_offline_payload(bundle)
            dependencies = status()
            restart = False
            if not all(dependencies.values()):
                power_shell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
                dependency_args = [power_shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                    '-File',str(dependency_helper),'-BundlePath',str(bundle)]
                if dependencies['webview2']:
                    dependency_args.append('-SkipWebView2')
                result = subprocess.run(dependency_args,capture_output=True,
                    timeout=900,creationflags=0x08000000)
                if result.returncode not in (0,3010):
                    raise ValueError('系统组件安装未完成。请允许管理员授权；安装包须完整解压。\n' + result.stderr.decode('utf-8',errors='replace')[-1200:])
                restart = result.returncode == 3010
            if not restart and not all(status().values()):
                raise ValueError('系统组件安装后检测未通过，未完成客户端安装。请检查 Windows 安装限制。')
            exe = install_files(bundle, destination, data, prepare_data=lambda _: helper_run('prepare'))
            warning = None
            if make_desktop:
                try:
                    helper_run('shortcut', '-Executable', str(exe))
                except Exception:
                    warning = '桌面快捷方式创建失败，可从安装路径打开客户端。'
            results.put(('ok',str(exe),restart,warning))
          except Exception as exc:
            results.put(('error',str(exc) if not isinstance(exc,subprocess.CalledProcessError) else '无法保护配置目录，请检查目录权限。'))
        installing = True
        install_button.configure(state='disabled');cancel_button.configure(state='disabled')
        entry.configure(state='disabled');browse_button.configure(state='disabled');desktop_button.configure(state='disabled')
        progress_text.set('正在安装，请等待；若弹出 Windows 管理员授权，请允许。')
        progress.start()
        threading.Thread(target=work,daemon=True).start()
        window.after(200,poll)
    def poll():
        nonlocal installing
        try:
            result=results.get_nowait()
        except queue.Empty:
            window.after(200,poll);return
        progress.stop();installing=False
        if result[0]=='ok':
            note='\n系统组件要求重启，请先保存工作并手动重启，再打开 Install.exe 完成复查。' if result[2] else '\n组件已就绪。打开客户端后添加订阅、手动开始入网。'
            if result[3]: note+='\n'+result[3]
            messagebox.showinfo('安装完成' if not result[2] else '需要重启完成安装','客户端文件已安装，尚未启动。\n'+result[1]+note,parent=window)
            window.destroy()
        else:
            messagebox.showerror('安装未完成',result[1],parent=window)
            install_button.configure(state='normal');cancel_button.configure(state='normal')
            entry.configure(state='normal');browse_button.configure(state='normal');desktop_button.configure(state='normal')
            progress_text.set(dependency_text())
    actions = ttk.Frame(frame)
    actions.pack(side='bottom', fill='x', pady=(20, 0))
    cancel_button=ttk.Button(actions,text='取消',command=window.destroy)
    cancel_button.pack(side='right')
    install_button=ttk.Button(actions,text='一键安装',command=install)
    install_button.pack(side='right',padx=10)
    def wrap_labels(event):
        for child in frame.winfo_children():
            if isinstance(child,ttk.Label):
                child.configure(wraplength=max(240,event.width-48))
    frame.bind('<Configure>',wrap_labels)
    window.protocol('WM_DELETE_WINDOW',lambda: None if installing else window.destroy())
    window.mainloop()


if __name__ == '__main__':
    main()
