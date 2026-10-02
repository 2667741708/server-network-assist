"""Prepare narrowly scoped native-window controls for the requested remote actor."""
from pathlib import Path
import json

repo = Path(__file__).resolve().parents[1]
root = Path(r'C:\Users\hmw20\.ssh\gui-d321-control-20260917')
root.mkdir(exist_ok=True)
old_root = r'C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917'
for source, target in [('titan_gui_inspect.ps1', 'inspect.ps1'), ('titan_gui_input.ps1', 'input.ps1')]:
    text = (repo / 'scripts' / source).read_text(encoding='utf-8-sig')
    text = text.replace("'DESKTOP-TD6B9GN'", "'WHM-SAVE'").replace('Titan only', 'WHM-SAVE only')
    text = text.replace(old_root, str(root))
    if target == 'input.ps1':
        old = "if ($owner.Path -notin @((Join-Path $root 'ServerNetworkAssistClient.exe'),(Join-Path $root 'refined\\ServerNetworkAssistClient.exe'))) { throw 'Unexpected GUI owner' }"
        if old not in text:
            raise RuntimeError('Input ownership guard changed: review generator')
        text = text.replace(old, "$settings = Get-Content -LiteralPath (Join-Path $root 'gui-config.json') -Raw | ConvertFrom-Json\nif ($owner.Path -ne $settings.exe) { throw 'Unexpected GUI owner' }")
        text = text.replace('$_.visible -and $_.width -eq 580 -and $_.height -eq 820', '$_.visible -and $_.width -ge 300 -and $_.height -ge 300')
        bounds = "if (-not [TitanGuiInput]::GetWindowRect($handle,[ref]$rect)) { throw 'Cannot read client bounds' }"
        if bounds not in text:
            raise RuntimeError('Input bounds guard changed: review generator')
        text = text.replace(bounds, bounds + "\nif (($rect.right-$rect.left) -ne $main.width -or ($rect.bottom-$rect.top) -ne $main.height) { throw 'Window bounds changed; inspect and view the fresh physical-pixel screenshot before input' }")
        text = text.replace('test-enrollment.txt', 'enrollment.txt').replace('Click-Client 150 752', 'Click-Client $X $Y')
    (root / target).write_text(text, encoding='utf-8-sig')
print(str(root))
