// Net-new because BTTCO had no existing vault-to-env bridge for Twilio.
// This mirrors GalaX's prelaunch pattern so the API keeps consuming plain
// TWILIO_* env vars while Hivemind vault stays canonical.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { homedir } from "node:os";
import { join } from "node:path";

const VAULT_KEY = "secrets:automation";
const MAPPING = {
  twilio_account_sid: "TWILIO_ACCOUNT_SID",
  twilio_auth_token: "TWILIO_AUTH_TOKEN",
  twilio_from_number: "TWILIO_FROM_NUMBER",
  twilio_messaging_service_sid: "TWILIO_MESSAGING_SERVICE_SID",
  twilio_webhook_auth_token: "TWILIO_WEBHOOK_AUTH_TOKEN",
};
const REQUIRED_FIELDS = [
  "twilio_account_sid",
  "twilio_auth_token",
  "twilio_from_number",
];

function getDragonflyUrl() {
  if (process.env.DRAGONFLY_URL) return process.env.DRAGONFLY_URL;
  const configPath = join(homedir(), ".codex", "config.toml");
  if (existsSync(configPath)) {
    const text = readFileSync(configPath, "utf8");
    const match = text.match(/^\s*DRAGONFLY_URL\s*=\s*"([^"]+)"/m);
    if (match) return match[1];
  }
  return "redis://localhost:6379";
}

function parseArgs(argv) {
  const opts = { format: "json", railway: false, service: "BTTCO" };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--json") opts.format = "json";
    else if (arg === "--dotenv") opts.format = "dotenv";
    else if (arg === "--powershell") opts.format = "powershell";
    else if (arg === "--railway") opts.railway = true;
    else if (arg === "--service" && argv[i + 1]) {
      opts.service = argv[i + 1];
      i += 1;
    } else if (arg === "--check") {
      opts.format = "json";
    } else {
      throw new Error(`Unknown arg: ${arg}`);
    }
  }
  return opts;
}

function parseRedisUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error(`bad DRAGONFLY_URL: ${url}`);
  }
  if (parsed.protocol !== "redis:") {
    throw new Error(`bad DRAGONFLY_URL protocol: ${parsed.protocol}`);
  }
  return {
    password: parsed.password ? decodeURIComponent(parsed.password) : null,
    host: parsed.hostname,
    port: parseInt(parsed.port || "6379", 10),
  };
}

function encodeCommand(args) {
  const parts = [`*${args.length}\r\n`];
  for (const arg of args) {
    const value = String(arg);
    parts.push(`$${Buffer.byteLength(value)}\r\n${value}\r\n`);
  }
  return Buffer.from(parts.join(""), "utf8");
}

function parseReply(buf, off) {
  if (off >= buf.length) return null;
  const type = String.fromCharCode(buf[off]);
  const nl = buf.indexOf("\r\n", off);
  if (nl < 0) return null;
  const head = buf.subarray(off + 1, nl).toString("utf8");
  if (type === "+" || type === "-" || type === ":") {
    return { value: type === "-" ? new Error(head) : head, consumed: nl + 2 };
  }
  if (type === "$") {
    const len = parseInt(head, 10);
    if (len === -1) return { value: null, consumed: nl + 2 };
    const dataStart = nl + 2;
    if (buf.length < dataStart + len + 2) return null;
    return {
      value: buf.subarray(dataStart, dataStart + len).toString("utf8"),
      consumed: dataStart + len + 2,
    };
  }
  if (type === "*") return { value: null, consumed: nl + 2 };
  throw new Error(`unhandled RESP type '${type}'`);
}

function redisFetchAll(host, port, password, hashKey, fields) {
  return new Promise((resolve, reject) => {
    const sock = createConnection({ host, port }, () => {
      const cmds = [];
      if (password) cmds.push(encodeCommand(["AUTH", password]));
      for (const field of fields) cmds.push(encodeCommand(["HGET", hashKey, field]));
      cmds.push(encodeCommand(["QUIT"]));
      sock.write(Buffer.concat(cmds));
    });
    let buf = Buffer.alloc(0);
    const replies = [];
    sock.on("data", (chunk) => {
      buf = Buffer.concat([buf, chunk]);
      while (true) {
        const parsed = parseReply(buf, 0);
        if (parsed === null) break;
        replies.push(parsed.value);
        buf = buf.subarray(parsed.consumed);
      }
    });
    sock.on("end", () => {
      if (password && replies[0] instanceof Error) {
        reject(new Error(`redis AUTH failed: ${replies[0].message}`));
        return;
      }
      const start = password ? 1 : 0;
      const hgets = replies.slice(start, start + fields.length);
      const map = {};
      for (let i = 0; i < fields.length; i += 1) {
        if (hgets[i] instanceof Error) {
          reject(new Error(`redis HGET ${fields[i]} failed: ${hgets[i].message}`));
          return;
        }
        map[fields[i]] = hgets[i];
      }
      resolve(map);
    });
    sock.on("error", reject);
    sock.setTimeout(5000, () => {
      sock.destroy(new Error("redis timeout"));
    });
  });
}

async function loadTwilioEnv() {
  const { host, port, password } = parseRedisUrl(getDragonflyUrl());
  const raw = await redisFetchAll(host, port, password, VAULT_KEY, Object.keys(MAPPING));
  const env = {};
  for (const [vaultName, envName] of Object.entries(MAPPING)) {
    const value = raw[vaultName];
    if (typeof value === "string" && value.length > 0) {
      env[envName] = value;
    }
  }
  const missingRequired = REQUIRED_FIELDS.filter((name) => !raw[name]);
  return { env, missingRequired };
}

function renderOutput(env, format) {
  if (format === "dotenv") {
    return `${Object.entries(env)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n")}\n`;
  }
  if (format === "powershell") {
    return `${Object.entries(env)
      .map(([k, v]) => `$env:${k}='${String(v).replace(/'/g, "''")}'`)
      .join("\n")}\n`;
  }
  return `${JSON.stringify(env, null, 2)}\n`;
}

function syncRailway(service, env) {
  const args = ["variable", "set", "-s", service, ...Object.entries(env).map(([k, v]) => `${k}=${v}`)];
  if (process.platform === "win32") {
    execFileSync("cmd.exe", ["/c", "railway", ...args], { stdio: "inherit" });
    return;
  }
  execFileSync("railway", args, { stdio: "inherit" });
}

try {
  const opts = parseArgs(process.argv.slice(2));
  const { env, missingRequired } = await loadTwilioEnv();
  if (missingRequired.length > 0) {
    console.error(`[twilio-vault-env] missing required vault keys: ${missingRequired.join(", ")}`);
    process.exit(2);
  }
  if (opts.railway) {
    syncRailway(opts.service, env);
  } else {
    process.stdout.write(renderOutput(env, opts.format));
  }
} catch (error) {
  console.error(`[twilio-vault-env] ${error.message}`);
  process.exit(1);
}
