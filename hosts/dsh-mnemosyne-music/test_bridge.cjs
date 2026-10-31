const test = require("node:test");
const assert = require("node:assert/strict");
const childProcess = require("child_process");
let launched;
childProcess.spawn = (...args) => { launched = args; return {on() {}}; };
const bridge = require("./index.js");

test("engine arguments are never evaluated by a shell", () => {
  bridge.runEngine(["doctor", "literal $(do-not-run); & value"], {stdio: "pipe"});
  assert.equal(launched[2].shell, false);
  assert.equal(launched[2].windowsHide, true);
  assert.equal(launched[1].at(-1), "literal $(do-not-run); & value");
  assert.equal(launched[2].env.MNEMOSYNE_MUSIC_HOST_MODE, "dsh");
});

test("business tools bind the owner and reject extra arguments", async () => {
  let request;
  global.fetch = async (...args) => { request = args; return {ok: true, json: async () => ({status: "success"})}; };
  const client = bridge.createToolClient({token: "synthetic-token", ownerUserId: "u1"});
  assert.equal(client.listTools().length, 10);
  assert.equal((await client.callTool("music_sync", {collectors: ["liked_songs"]})).status, "success");
  assert.equal(JSON.parse(request[1].body).owner_user_id, "u1");
  assert.equal(request[1].redirect, "error");
  await assert.rejects(client.callTool("music_sync", {owner_user_id: "other"}));
  await assert.rejects(client.callTool("music_auth_cookie", {cookie: "forbidden"}));
});
