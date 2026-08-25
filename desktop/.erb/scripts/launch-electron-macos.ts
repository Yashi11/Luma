import { ChildProcess, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const desktopRoot = path.resolve(__dirname, '../..');
const electronDistPath = path.join(
  desktopRoot,
  'node_modules',
  'electron',
  'dist',
);
let electronBinary = path.join(electronDistPath, 'electron');
if (process.platform === 'win32') {
  electronBinary = path.join(electronDistPath, 'electron.exe');
} else if (process.platform === 'darwin') {
  electronBinary = path.join(
    electronDistPath,
    'Electron.app',
    'Contents',
    'MacOS',
    'Electron',
  );
}
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
  // Launch Electron directly on macOS. Using `/usr/bin/open` for the nested
  // Electron.app can fail with LaunchServices -10810 before the main process
  // starts, leaving webpack running with no desktop window. Direct execution
  // also gives the launcher the real child PID for reliable hot reload and
  // shutdown handling.
  const args = [entryPath, ...process.argv.slice(2)];
  const env = {
    ...process.env,
    NODE_ENV: 'development',
    NODE_OPTIONS: '',
    COCO_DEV_PID_PATH: pidPath,
  };
  launcher = spawn(electronBinary, args, {
    cwd: desktopRoot,
    stdio: 'inherit',
    env,
  });
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
