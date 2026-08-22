import { ChildProcess, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const desktopRoot = path.resolve(__dirname, '../..');
const electronApp = path.join(
  desktopRoot,
  'node_modules',
  'electron',
  'dist',
  'Electron.app',
);
const electronBinary =
  process.platform === 'win32'
    ? path.join(desktopRoot, 'node_modules', 'electron', 'dist', 'electron.exe')
    : path.join(desktopRoot, 'node_modules', 'electron', 'dist', 'electron');
const entryPath = path.join(__dirname, 'launch-services-entry.cjs');
const pidPath = path.join(desktopRoot, '.erb', 'dll', 'coco-dev.pid');
const stdoutPath = path.join(desktopRoot, '.erb', 'dll', 'coco-dev.stdout.log');
const stderrPath = path.join(desktopRoot, '.erb', 'dll', 'coco-dev.stderr.log');
const watchedBundles = [
  path.join(desktopRoot, '.erb', 'dll', 'main.bundle.dev.js'),
  path.join(desktopRoot, '.erb', 'dll', 'preload.bundle.dev.js'),
];

let launcher: ChildProcess | null = null;
let restartTimer: ReturnType<typeof setTimeout> | null = null;
let restartRequested = false;
let stopping = false;

function readAppPid(): number | null {
  try {
    const pid = Number(fs.readFileSync(pidPath, 'utf8').trim());
    return Number.isSafeInteger(pid) && pid > 1 ? pid : null;
  } catch {
    return null;
  }
}

function stopApp(): void {
  const pid = readAppPid();
  if (!pid) return;
  try {
    process.kill(pid, 'SIGINT');
  } catch (error) {
    if ((error as { code?: string }).code !== 'ESRCH') throw error;
  }
}

function launch(): void {
  fs.rmSync(pidPath, { force: true });
  fs.writeFileSync(stdoutPath, '');
  fs.writeFileSync(stderrPath, '');
  const args =
    process.platform === 'darwin'
      ? [
          '-W',
          '-n',
          '-o',
          stdoutPath,
          '--stderr',
          stderrPath,
          '--env',
          'NODE_ENV=development',
          '--env',
          'NODE_OPTIONS=',
          '--env',
          `COCO_DEV_PID_PATH=${pidPath}`,
          electronApp,
          '--args',
          entryPath,
          ...process.argv.slice(2),
        ]
      : [entryPath, ...process.argv.slice(2)];
  const executable =
    process.platform === 'darwin' ? '/usr/bin/open' : electronBinary;
  launcher = spawn(executable, args, { cwd: desktopRoot, stdio: 'inherit' });
  launcher.once('exit', (code, signal) => {
    launcher = null;
    fs.rmSync(pidPath, { force: true });
    if (stopping) process.exit(0);
    if (restartRequested) {
      restartRequested = false;
      launch();
      return;
    }
    const stderr = fs.readFileSync(stderrPath, 'utf8').trim();
    if (stderr) console.error(stderr);
    console.log(
      `[electron-launcher] app exited code=${code} signal=${signal}; waiting for a main-process change`,
    );
  });
}

function scheduleRestart(): void {
  if (stopping) return;
  if (restartTimer) clearTimeout(restartTimer);
  restartTimer = setTimeout(() => {
    restartTimer = null;
    restartRequested = true;
    if (launcher) stopApp();
    else {
      restartRequested = false;
      launch();
    }
  }, 250);
}

watchedBundles.forEach((bundle) => {
  fs.watchFile(bundle, { interval: 500 }, (current, previous) => {
    if (current.mtimeMs !== previous.mtimeMs) scheduleRestart();
  });
});

function shutdown(): void {
  if (stopping) return;
  stopping = true;
  if (restartTimer) clearTimeout(restartTimer);
  watchedBundles.forEach((bundle) => fs.unwatchFile(bundle));
  stopApp();
  if (!launcher) process.exit(0);
}

process.once('SIGINT', shutdown);
process.once('SIGTERM', shutdown);
launch();
