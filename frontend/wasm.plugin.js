// Build target "wasm" (npm run build:wasm / dev:wasm): packs Noah's Python (noah/) and the shipped experts (experts/) into
// noah-py.zip next to the page. The worker (src/wasm/worker.js) unpacks it into Pyodide. Written by hand to need no dependency.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { crc32, deflateRawSync } from 'node:zlib';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SKIP = new Set(['__pycache__', 'web']);   // noah/web is the built UI, which the browser already has

function* walk(dir) {
  for (const name of readdirSync(dir).sort()) {
    if (name.startsWith('.') || SKIP.has(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* walk(path);
    else yield path;
  }
}

function zip(files) {   // [name, Buffer] -> a .zip (deflate), enough for Python's zipfile
  const parts = [], central = [];
  let offset = 0;
  for (const [name, data] of files) {
    const n = Buffer.from(name), packed = deflateRawSync(data), crc = crc32(data);
    const head = Buffer.alloc(30);
    head.writeUInt32LE(0x04034b50, 0); head.writeUInt16LE(20, 4); head.writeUInt16LE(0x0800, 6); head.writeUInt16LE(8, 8);
    head.writeUInt16LE(0x21, 12); head.writeUInt32LE(crc, 14); head.writeUInt32LE(packed.length, 18); head.writeUInt32LE(data.length, 22);
    head.writeUInt16LE(n.length, 26);
    const dir = Buffer.alloc(46);
    dir.writeUInt32LE(0x02014b50, 0); dir.writeUInt16LE(20, 4); dir.writeUInt16LE(20, 6); dir.writeUInt16LE(0x0800, 8); dir.writeUInt16LE(8, 10);
    dir.writeUInt16LE(0x21, 14); dir.writeUInt32LE(crc, 16); dir.writeUInt32LE(packed.length, 20); dir.writeUInt32LE(data.length, 24);
    dir.writeUInt16LE(n.length, 28); dir.writeUInt32LE(offset, 42);
    parts.push(head, n, packed); central.push(dir, n);
    offset += head.length + n.length + packed.length;
  }
  const cd = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, cd, end]);
}

export function pythonZip() {
  const files = [];
  for (const top of ['noah', 'experts']) {
    for (const path of walk(join(ROOT, top))) {
      if (top === 'noah' && !path.endsWith('.py')) continue;
      files.push([relative(ROOT, path).split(sep).join('/'), readFileSync(path)]);
    }
  }
  return zip(files);
}

export default function noahWasm() {
  return {
    name: 'noah-wasm',
    configureServer(server) {   // dev: always the current sources (reload the page after changing Python)
      server.middlewares.use('/noah-py.zip', (_req, res) => { res.setHeader('Content-Type', 'application/zip'); res.end(pythonZip()); });
    },
    generateBundle() { this.emitFile({ type: 'asset', fileName: 'noah-py.zip', source: pythonZip() }); },
  };
}
