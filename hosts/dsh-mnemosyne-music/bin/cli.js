#!/usr/bin/env node
"use strict";

const bridge = require("../index.js");
const args = process.argv.slice(2);
if (args[0] === "doctor" || args.length === 0) {
  const child = bridge.doctor();
  child.on("exit", (code) => process.exit(code || 0));
} else if (args[0] === "bootstrap") {
  const child = bridge.bootstrap(args.slice(1));
  child.on("exit", (code) => process.exit(code || 0));
} else {
  const child = bridge.runPython(args);
  child.on("exit", (code) => process.exit(code || 0));
}
