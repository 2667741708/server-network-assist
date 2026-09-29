"""Native Windows WLAN scan; never connect, disconnect or edit Wi-Fi profiles."""
import ctypes as c
import os
import time


U32 = c.c_uint32


class GUID(c.Structure):
    _fields_ = [('first', U32), ('second', c.c_uint16), ('third', c.c_uint16),
                ('last', c.c_ubyte * 8)]


class Interface(c.Structure):
    _fields_ = [('guid', GUID), ('description', c.c_uint16 * 256), ('state', U32)]


class SSID(c.Structure):
    _fields_ = [('length', U32), ('data', c.c_ubyte * 32)]


class Network(c.Structure):
    _fields_ = [('profile', c.c_uint16 * 256), ('ssid', SSID), ('bss_type', U32),
                ('bssids', U32), ('connectable', U32), ('reason', U32), ('phy_count', U32),
                ('phy_types', U32 * 8), ('more_phy', U32), ('signal', U32), ('secure', U32),
                ('auth', U32), ('cipher', U32), ('flags', U32), ('reserved', U32)]


def _wide(value):
    return bytes(value).decode('utf-16-le', 'replace').split('\0', 1)[0]


def _rows(pointer, kind, maximum):
    count = U32.from_address(pointer.value).value
    if count > maximum:
        raise ValueError('无线扫描返回的条目数量无效')
    return [kind.from_buffer_copy(c.string_at(pointer.value + 8 + i * c.sizeof(kind), c.sizeof(kind)))
            for i in range(count)]


def _error(code):
    if code == 5:
        return 'Windows 拒绝读取无线信息；请在系统设置的隐私和安全性中允许位置访问，然后重新扫描。'
    if code == 1062:
        return 'Windows WLAN AutoConfig 服务未运行，请检查系统无线服务。'
    return f'无线扫描未完成（Windows 错误 {code}），请检查无线网卡和无线开关。'


def scan():
    result = {'supported': os.name == 'nt', 'networks': [], 'message': '无线扫描暂适用于 Windows。'}
    if os.name != 'nt':
        return result
    try:
        wlan = c.WinDLL('wlanapi.dll')
    except OSError:
        return {**result, 'message': '此 Windows 系统没有可用的 WLAN API；请检查无线功能组件。'}
    pointer = c.c_void_p
    guid = c.POINTER(GUID)
    signatures = {
        'WlanOpenHandle': [U32, pointer, c.POINTER(U32), c.POINTER(pointer)],
        'WlanEnumInterfaces': [pointer, pointer, c.POINTER(pointer)],
        'WlanScan': [pointer, guid, pointer, pointer, pointer],
        'WlanGetAvailableNetworkList': [pointer, guid, U32, pointer, c.POINTER(pointer)],
        'WlanFreeMemory': [pointer], 'WlanCloseHandle': [pointer, pointer],
    }
    for name, args in signatures.items():
        function = getattr(wlan, name)
        function.argtypes = args
        function.restype = None if name == 'WlanFreeMemory' else U32
    handle, version, interfaces = pointer(), U32(), pointer()
    code = wlan.WlanOpenHandle(2, None, c.byref(version), c.byref(handle))
    if code:
        return {**result, 'message': _error(code)}
    errors = []
    try:
        code = wlan.WlanEnumInterfaces(handle, None, c.byref(interfaces))
        if code:
            return {**result, 'message': _error(code)}
        adapters = _rows(interfaces, Interface, 64)
        if not adapters:
            return {**result, 'message': '未发现可用无线网卡；有线接入仍可检查校园网络。'}
        requested = False
        for adapter in adapters:
            code = wlan.WlanScan(handle, c.byref(adapter.guid), None, None, None)
            if code:
                errors.append(_error(code))
            else:
                requested = True
        if requested:
            # WlanScan is asynchronous. The driver may retain older results;
            # present this as the latest Windows list, not proof of freshness.
            time.sleep(4)
        for adapter in adapters:
            networks = pointer()
            try:
                code = wlan.WlanGetAvailableNetworkList(handle, c.byref(adapter.guid), 0, None, c.byref(networks))
                if code:
                    errors.append(_error(code))
                    continue
                for row in _rows(networks, Network, 4096):
                    raw = bytes(row.ssid.data)[:min(row.ssid.length, 32)]
                    result['networks'].append({'ssid': raw.decode('utf-8', 'replace') or '隐藏网络',
                        'interface': _wide(adapter.description), 'signal': min(row.signal, 100),
                        'connected': bool(row.flags & 1), 'secure': bool(row.secure),
                        'connectable': bool(row.connectable)})
            finally:
                if networks.value:
                    wlan.WlanFreeMemory(networks)
        result['networks'].sort(key=lambda row: (not row['connected'], -row['signal'], row['ssid']))
        result['message'] = '\n'.join(dict.fromkeys(errors)) if errors else '已读取 Windows 最新无线列表，可能包含缓存结果；无线名称不能证明校园局域网可达。请自行连接后检查接入。'
        return result
    finally:
        if interfaces.value:
            wlan.WlanFreeMemory(interfaces)
        wlan.WlanCloseHandle(handle, None)
