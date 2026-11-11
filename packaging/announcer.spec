# PyInstaller runs this file as Python:
#
#     uv run pyinstaller packaging/announcer.spec
#
# It writes ``dist/Kleomarcus Announcer.app`` on macOS and the folder
# ``dist/Kleomarcus Announcer`` on Windows, which ``installer.iss`` turns into
# a setup program.

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

from announcer import APP_NAME
from announcer.logo import MAC_MARGIN, render_ico, render_png

PACKAGE = Path(SPECPATH).parent / "src" / "announcer"
VERSION = version("kleomarcus-announcer")
ICONS = Path(workpath) / "icons"  # ``installer.iss`` takes its icon from here


def windows_icon():
    path = ICONS / "icon.ico"
    path.write_bytes(render_ico())
    return path


def mac_icon():
    iconset = ICONS / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    sizes = (16, 32, 64, 128, 256, 512, 1024)  # pixels a side
    images = {size: render_png(size, MAC_MARGIN) for size in sizes}
    for points in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            name = f"icon_{points}x{points}{suffix}.png"
            (iconset / name).write_bytes(images[points * scale])
    subprocess.run(["iconutil", "--convert", "icns", iconset], check=True)
    return iconset.with_suffix(".icns")


def windows_version_info():
    """What Windows shows in the Details tab of the file's properties."""
    # Importable on Windows only.
    from PyInstaller.utils.win32 import versioninfo

    numbers = (*(int(part) for part in VERSION.split(".")), 0)
    texts = {
        "FileDescription": APP_NAME,
        "FileVersion": VERSION,
        "OriginalFilename": f"{APP_NAME}.exe",
        "ProductName": APP_NAME,
        "ProductVersion": VERSION,
    }
    table = versioninfo.StringTable(
        "040904B0",  # US English, Unicode
        [versioninfo.StringStruct(name, text) for name, text in texts.items()],
    )
    return versioninfo.VSVersionInfo(
        ffi=versioninfo.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            versioninfo.StringFileInfo([table]),
            versioninfo.VarFileInfo(
                [versioninfo.VarStruct("Translation", [0x0409, 1200])]
            ),
        ],
    )


ICONS.mkdir(parents=True, exist_ok=True)
on_mac = sys.platform == "darwin"
icon = str(mac_icon() if on_mac else windows_icon())

analysis = Analysis(
    [str(PACKAGE / "__main__.py")],
    datas=[
        (str(PACKAGE / "assets"), "announcer/assets"),
        # The app reads its own version from this.
        *copy_metadata("kleomarcus-announcer"),
    ],
)
executable = EXE(
    PYZ(analysis.pure),
    analysis.scripts,
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    upx=False,
    icon=icon,
    version=None if on_mac else windows_version_info(),
)
folder = COLLECT(
    executable, analysis.binaries, analysis.datas, name=APP_NAME, upx=False
)
if on_mac:
    BUNDLE(
        folder,
        name=f"{APP_NAME}.app",
        icon=icon,
        bundle_identifier="com.kleomarcus.announcer",
        version=VERSION,
    )
