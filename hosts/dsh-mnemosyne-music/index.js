"use strict";

/**
 * Minimal DSH host bridge: spawn Python music_acquisition CLI / doctor.
 * Does NOT embed acquisition logic. Requires Python package on PYTHONPATH.
 */

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const toolDefinitions = require("./business-tools.json");

function repoSrc() {
  return path.resolve(__dirname, "..", "..", "src");
}

function runPython(args, opts = {}) {
  const env = {
    ...process.env,
    MNEMOSYNE_MUSIC_HOST_MODE: "dsh",
    PYTHONPATH: [repoSrc(), process.env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter),
  };
  const candidates = [process.env.MNEMOSYNE_MUSIC_EXE,
    path.join(__dirname, "runtime", "mnemosyne-music", "mnemosyne-music.exe"),
    path.resolve(__dirname, "../../release/windows/mnemosyne-music/mnemosyne-music.exe")].filter(Boolean);
  const bundled = candidates.find(file => fs.existsSync(file));
  const py = bundled || process.env.MNEMOSYNE_PYTHON || process.env.PYTHON || "py";
  const base = bundled ? [...args] : py === "py" ? ["-3", "-m", "music_acquisition.cli", ...args] : ["-m", "music_acquisition.cli", ...args];
  const child = spawn(py, base, {
    env,
    stdio: opts.stdio || "inherit",
    shell: false,
    windowsHide: true,
  });
  return child;
}

function createToolClient({baseUrl = "http://127.0.0.1:8770", token = process.env.MNEMOSYNE_MUSIC_AGENT_TOKEN, ownerUserId = "owner"} = {}) {
  if (!token) throw new Error("MNEMOSYNE_MUSIC_AGENT_TOKEN_required");
  return {
    listTools: () => toolDefinitions.map(({name, inputSchema}) => ({name, inputSchema})),
    async callTool(name, args = {}) {
      const spec = toolDefinitions.find(tool => tool.name === name);
      if (!spec || !args || typeof args !== "object" || Array.isArray(args)) throw new Error("invalid_tool");
      if (Object.keys(args).some(key => !Object.prototype.hasOwnProperty.call(spec.inputSchema.properties, key)) || spec.inputSchema.required.some(key => !Object.prototype.hasOwnProperty.call(args, key))) throw new Error("invalid_arguments");
      for (const [key, value] of Object.entries(args)) {
        const type = spec.inputSchema.properties[key].type;
        if ((type === "integer" && !Number.isInteger(value)) || (type === "array" && (!Array.isArray(value) || value.some(v => typeof v !== "string"))) || (["string", "boolean"].includes(type) && typeof value !== type)) throw new Error("invalid_argument_type");
      }
      const values = {...args};
      if (name === "music_sync") values.owner_user_id = ownerUserId;
      const url = new URL("/api/music/v1/" + spec.path, baseUrl);
      if (!spec.body) for (const [key, value] of Object.entries(values)) url.searchParams.set(key, String(value));
      const response = await fetch(url, {method: spec.method, redirect: "error", signal: AbortSignal.timeout(60000),
        headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"},
        ...(spec.body ? {body: JSON.stringify(values)} : {})});
      if (!response.ok) return {ok: false, error: "HTTP_" + response.status};
      return response.json();
    }
  };
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
  runEngine: runPython,
  createToolClient,
  toolDefinitions,
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
      runtime: "bundled-engine-or-host-python",
      note: "The same Engine runs under DshHostRuntime. Use createToolClient or MCP for business tools; management remains owner-only.",
    };
  },
};
