const fs = require('fs');
const path = require('path');

const desktopRoot = path.resolve(__dirname, '../..');
const pidPath = process.env.COCO_DEV_PID_PATH;

process.chdir(desktopRoot);
if (pidPath) {
  fs.writeFileSync(pidPath, String(process.pid));
  process.once('exit', () => {
    try {
      if (fs.readFileSync(pidPath, 'utf8').trim() === String(process.pid)) {
        fs.rmSync(pidPath, { force: true });
      }
    } catch {
      // Another launch may already own or remove the rendezvous file.
    }
  });
}

require(path.join(desktopRoot, '.erb', 'dll', 'main.bundle.dev.js'));
