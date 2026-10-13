#!/bin/zsh
set -eu
cd "${0:A:h}"
mkdir -p .launcher-build
swiftc launcher/main.swift -o .launcher-build/launch
swift launcher/make_icon.swift .launcher-build/Shengying.iconset
iconutil -c icns .launcher-build/Shengying.iconset -o .launcher-build/Shengying.icns
BUILD_PYTHON="${SHENGYING_PYTHON:-$PWD/.venv/bin/python}"
"$BUILD_PYTHON" - <<'PY'
from pathlib import Path
import plistlib, re, shutil
root = Path.cwd()
version = re.search(r'VERSION = "([^"]+)"', (root / 'records.py').read_text()).group(1)
contents = root / '听文声影工作台.app' / 'Contents'
for directory in ['MacOS', 'Resources']:
    (contents / directory).mkdir(parents=True, exist_ok=True)
shutil.copy2(root / '.launcher-build/launch', contents / 'MacOS/launch')
shutil.copy2(root / '.launcher-build/Shengying.icns', contents / 'Resources/Shengying.icns')
(contents / 'Resources/product-root.txt').write_text(str(root) + '\n')
info = {'CFBundleName': '听文声影工作台', 'CFBundleDisplayName': '听文声影工作台',
        'CFBundleIdentifier': 'local.tingwen.shengying.workbench', 'CFBundleExecutable': 'launch',
        'CFBundleIconFile': 'Shengying.icns', 'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': version, 'CFBundleVersion': version, 'LSUIElement': True}
(contents / 'Info.plist').write_bytes(plistlib.dumps(info))
PY
codesign --force --deep --sign - 听文声影工作台.app
echo '构建完成：听文声影工作台.app'
