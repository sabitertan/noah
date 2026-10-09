// Starts Electron with a clean environment. VS Code's extension host (and some terminals started from it)
// export ELECTRON_RUN_AS_NODE=1, which makes the electron binary behave as plain Node and breaks the app.
const { spawn } = require('node:child_process');
const electron = require('electron');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
// Electron's native Wayland path segfaults on some NVIDIA/Wayland setups (seen on Electron 38), so Linux defaults to
// X11/XWayland. It must be a command-line flag; packaged builds get it from after-pack.js. NOAH_OZONE_PLATFORM overrides.
const platformArgs = process.platform === 'linux' ? [`--ozone-platform=${process.env.NOAH_OZONE_PLATFORM || 'x11'}`] : [];
const child = spawn(electron, ['.', ...platformArgs, ...process.argv.slice(2)], { stdio: 'inherit', env, cwd: __dirname });
child.on('exit', code => process.exit(code ?? 0));
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
