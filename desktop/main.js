// Noah desktop shell. Starts the Python server on a random loopback port with a per-launch secret,
// then shows the web UI in a locked-down window. The server is the same one `make up` runs.
const { app, BrowserWindow, dialog, shell, session } = require('electron');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const REPO = path.resolve(__dirname, '..');
const TOKEN = crypto.randomBytes(32).toString('hex');
const SMOKE = process.argv.includes('--smoke');
let server = null;
let origin = null;
let quitting = false;

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

// Dev: repo .venv (or NOAH_PYTHON). Packaged: the PyInstaller sidecar placed in resources/noah-server.
function serverCommand(port) {
  const sidecar = path.join(process.resourcesPath || '', 'noah-server', process.platform === 'win32' ? 'noah-server.exe' : 'noah-server');
  if (app.isPackaged && fs.existsSync(sidecar)) return { cmd: sidecar, args: ['--port', String(port)], cwd: path.dirname(sidecar) };
  const venvPy = process.platform === 'win32' ? path.join(REPO, '.venv', 'Scripts', 'python.exe') : path.join(REPO, '.venv', 'bin', 'python');
  const python = process.env.NOAH_PYTHON || (fs.existsSync(venvPy) ? venvPy : 'python3');
  return { cmd: python, args: ['-m', 'uvicorn', 'noah.server:app', '--host', '127.0.0.1', '--port', String(port)], cwd: REPO };
}

async function waitReady(port, ms = 90000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (server && server.exitCode !== null) throw new Error(`server exited with code ${server.exitCode}`);
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/config`, { headers: { Authorization: `Bearer ${TOKEN}` } });
      if (r.ok) return;
    } catch (e) { /* not up yet */ }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error('server did not start in time');
}

async function startServer() {
  const port = await freePort();
  const { cmd, args, cwd } = serverCommand(port);
  const data = path.join(app.getPath('userData'), 'data');
  fs.mkdirSync(data, { recursive: true });
  let packagedEnv = {};
  if (app.isPackaged) {
    // user-editable copies live in userData so upgrades never overwrite them; defaults are seeded on first run
    const experts = path.join(app.getPath('userData'), 'experts');
    if (!fs.existsSync(experts)) fs.cpSync(path.join(process.resourcesPath, 'experts'), experts, { recursive: true });
    const files = path.join(data, 'files');
    if (!fs.existsSync(files)) fs.cpSync(path.join(process.resourcesPath, 'sample-files'), files, { recursive: true });
    packagedEnv = { NOAH_EXPERTS: experts, NOAH_PROFILES: path.join(data, 'profiles.json'),
      NOAH_TRACES: path.join(data, 'traces.jsonl'), NOAH_FILES_DIR: files,
      NOAH_HF_TOKEN_FILE: path.join(data, 'hf_token'), NOAH_HF_MODELS: path.join(data, 'hf_models.json'),
      NOAH_OR_TOKEN_FILE: path.join(data, 'openrouter_key'), NOAH_OR_MODELS: path.join(data, 'openrouter_models.json'),
      NOAH_AZ_TOKEN_FILE: path.join(data, 'azure_key'), NOAH_AZ_SETTINGS: path.join(data, 'azure_foundry.json'), NOAH_AZ_MODELS: path.join(data, 'azure_models.json'),
      FASTEMBED_CACHE_PATH: path.join(app.getPath('userData'), 'models') };
  }
  server = spawn(cmd, args, {
    cwd,
    env: { ...process.env, NOAH_LAUNCH_TOKEN: TOKEN, NOAH_PARENT_PID: String(process.pid), ...packagedEnv },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', d => process.stdout.write(`[server] ${d}`));
  server.stderr.on('data', d => process.stderr.write(`[server] ${d}`));
  server.on('exit', code => { if (!quitting) { console.error(`[server] exited (${code})`); } });
  await waitReady(port);
  origin = `http://127.0.0.1:${port}`;
}

async function checkOllama() {
  try { await fetch('http://127.0.0.1:11434/api/version', { signal: AbortSignal.timeout(1500) }); return true; } catch (e) { return false; }
}

async function createWindow() {
  // the secret travels as an HttpOnly, SameSite=Strict cookie: page scripts and other sites never see it
  await session.defaultSession.cookies.set({ url: origin, name: 'noah_token', value: TOKEN, httpOnly: true, sameSite: 'strict' });
  const win = new BrowserWindow({
    width: 1280, height: 820, minWidth: 720, minHeight: 520, backgroundColor: '#282c34', show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.once('ready-to-show', () => win.show());
  // never navigate away from Noah inside the app window; external links open in the system browser
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith(origin)) { e.preventDefault(); shell.openExternal(url); } });
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  await win.loadURL(origin);
  return win;
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();
app.on('second-instance', () => { const w = BrowserWindow.getAllWindows()[0]; if (w) { if (w.isMinimized()) w.restore(); w.focus(); } });

app.whenReady().then(async () => {
  try {
    await startServer();
    const win = await createWindow();
    if (SMOKE) {
      await new Promise(r => setTimeout(r, 800));
      if (process.env.NOAH_SMOKE_SHOT) fs.writeFileSync(process.env.NOAH_SMOKE_SHOT, (await win.webContents.capturePage()).toPNG());
      console.log(`SMOKE_OK ${await win.webContents.getTitle()}`);
      app.quit();
      return;
    }
    if (!(await checkOllama())) {
      const r = await dialog.showMessageBox(win, { type: 'info', buttons: ['Get Ollama', 'Continue'], defaultId: 1,
        message: 'Ollama was not detected on this computer.',
        detail: 'Noah runs its small models through Ollama (or any OpenAI-compatible server on port 11434). Rule-based experts work without it.' });
      if (r.response === 0) shell.openExternal('https://ollama.com/download');
    }
  } catch (err) {
    dialog.showErrorBox('Noah could not start', String(err.message || err));
    app.quit();
  }
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { quitting = true; if (server && server.exitCode === null) server.kill(); });
