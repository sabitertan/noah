// electron-builder afterPack hook (Linux only): swap the real binary for a tiny wrapper that adds --ozone-platform.
// Electron's native Wayland path segfaults on some NVIDIA/Wayland setups, and the flag must be on the command line
// (an AppImage has no .desktop Exec line to carry it, and relaunching from JS breaks the AppImage mount).
// Opt out with NOAH_OZONE_PLATFORM=wayland|auto.
const fs = require('node:fs');
const path = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'linux') return;
  const exe = context.packager.executableName;
  const real = path.join(context.appOutDir, exe);
  fs.renameSync(real, `${real}.bin`);
  fs.writeFileSync(real, `#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/${exe}.bin" --ozone-platform="\${NOAH_OZONE_PLATFORM:-x11}" "$@"\n`, { mode: 0o755 });
};
