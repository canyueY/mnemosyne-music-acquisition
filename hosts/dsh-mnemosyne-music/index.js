"use strict";

/**
 * Minimal DSH host bridge: spawn Python music_acquisition CLI / doctor.
 * Does NOT embed acquisition logic. Requires Python package on PYTHONPATH.
 */

const { spawn } = require("child_process");
const path = require("path");

function repoSrc() {
  return path.resolve(__dirname, "..", "..", "src");
}

function runPython(args, opts = {}) {
  const env = {
    ...process.env,
    PYTHONPATH: [repoSrc(), process.env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter),
  };
  const py = process.env.PYTHON || process.env.MNEMOSYNE_PYTHON || "py";
  const base = py === "py" ? ["-3", "-m", "music_acquisition.cli", ...args] : ["-m", "music_acquisition.cli", ...args];
  const child = spawn(py, base, {
    env,
    stdio: opts.stdio || "inherit",
    shell: process.platform === "win32",
  });
  return child;
}

function doctor() {
  return runPython(["doctor"]);
}

function bootstrap(extraArgs = []) {
  return runPython(["bootstrap", ...extraArgs]);
}

module.exports = {
  name: "dsh-mnemosyne-music",
  version: require("./package.json").version,
  runPython,
  doctor,
  bootstrap,
  apply(ctx) {
    // Cordis/DSH optional hook: register a command if ctx.command exists.
    if (ctx && typeof ctx.command === "function") {
      ctx.command("music-acq-doctor", "Mnemosyne music acquisition doctor").action(() => {
        doctor();
        return "music acquisition doctor started";
      });
    }
    return {
      name: "dsh-mnemosyne-music",
      runtime: "python-bridge",
      note: "HostRuntime is Python Filesystem/DshHost; this package only spawns CLI.",
    };
  },
};
