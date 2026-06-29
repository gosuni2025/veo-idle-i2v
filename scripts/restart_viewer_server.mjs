#!/usr/bin/env node
import { spawn, execFile } from "node:child_process";
import { closeSync, openSync } from "node:fs";
import { access, mkdir, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 8767;

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const serverScript = path.join(projectDir, "scripts/viewer_server.mjs");

const options = await parseArgs(process.argv.slice(2));
await stopPort(options.port);
await startServer(options);

async function parseArgs(argv) {
  const parsed = {
    root: "",
    host: DEFAULT_HOST,
    port: DEFAULT_PORT,
    foreground: false,
    log: "",
  };

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--host") {
      parsed.host = argv[index + 1] || parsed.host;
      index += 1;
    } else if (value === "--port") {
      parsed.port = Number(argv[index + 1] || parsed.port);
      index += 1;
    } else if (value === "--foreground") {
      parsed.foreground = true;
    } else if (value === "--log") {
      parsed.log = argv[index + 1] || parsed.log;
      index += 1;
    } else if (!parsed.root) {
      parsed.root = value;
    }
  }

  if (!Number.isInteger(parsed.port) || parsed.port <= 0) {
    throw new Error(`Invalid port: ${parsed.port}`);
  }
  assertLoopbackHost(parsed.host);

  parsed.root = path.resolve(projectDir, parsed.root || await latestViewerRoot());
  parsed.log = path.resolve(projectDir, parsed.log || path.join(".tmp", `viewer-server-${parsed.port}.log`));
  await assertViewerRoot(parsed.root);
  return parsed;
}

function assertLoopbackHost(value) {
  const allowed = new Set(["127.0.0.1", "localhost", "::1"]);
  if (!allowed.has(String(value))) {
    throw new Error(`Viewer server is loopback-only. Unsupported host: ${value}`);
  }
}

async function latestViewerRoot() {
  const spritesRoot = path.join(projectDir, "outputs/sprites-pymatting");
  try {
    const entries = await readdir(spritesRoot, { withFileTypes: true });
    const candidates = [];
    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }
      const root = path.join(spritesRoot, entry.name);
      const viewer = path.join(root, "viewer.html");
      try {
        const info = await stat(viewer);
        candidates.push({ root, time: info.mtimeMs });
      } catch (error) {
        if (!error || error.code !== "ENOENT") {
          throw error;
        }
      }
    }
    candidates.sort((a, b) => b.time - a.time);
    if (candidates.length) {
      return candidates[0].root;
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }
  return projectDir;
}

async function assertViewerRoot(root) {
  await access(path.join(root, "viewer.html"));
}

async function stopPort(port) {
  const pids = await pidsOnPort(port);
  if (!pids.length) {
    console.log(`No existing viewer server on port ${port}`);
    return;
  }

  console.log(`Stopping port ${port}: ${pids.join(", ")}`);
  for (const pid of pids) {
    try {
      process.kill(pid, "SIGTERM");
    } catch (error) {
      if (!error || error.code !== "ESRCH") {
        throw error;
      }
    }
  }

  const stopped = await waitFor(async () => (await pidsOnPort(port)).length === 0, 4000);
  if (stopped) {
    return;
  }

  const remaining = await pidsOnPort(port);
  if (!remaining.length) {
    return;
  }
  console.log(`Force stopping port ${port}: ${remaining.join(", ")}`);
  for (const pid of remaining) {
    try {
      process.kill(pid, "SIGKILL");
    } catch (error) {
      if (!error || error.code !== "ESRCH") {
        throw error;
      }
    }
  }
  const killed = await waitFor(async () => (await pidsOnPort(port)).length === 0, 4000);
  if (!killed) {
    throw new Error(`Could not free port ${port}`);
  }
}

async function startServer(options) {
  const args = [
    serverScript,
    options.root,
    "--host",
    options.host,
    "--port",
    String(options.port),
  ];

  if (options.foreground) {
    console.log(`Starting viewer server in foreground: http://${options.host}:${options.port}/viewer.html`);
    const child = spawn(process.execPath, args, {
      cwd: projectDir,
      stdio: "inherit",
    });
    child.on("exit", (code, signal) => {
      process.exitCode = code ?? (signal ? 1 : 0);
    });
    return;
  }

  await mkdir(path.dirname(options.log), { recursive: true });
  const logFd = openSync(options.log, "a");
  const child = spawn(process.execPath, args, {
    cwd: projectDir,
    detached: true,
    stdio: ["ignore", logFd, logFd],
  });
  child.unref();
  closeSync(logFd);

  const listening = await waitFor(async () => (await pidsOnPort(options.port)).length > 0, 5000);
  if (!listening) {
    throw new Error(`Viewer server did not start on port ${options.port}. See ${options.log}`);
  }

  const ok = await waitFor(async () => {
    try {
      const response = await fetch(`http://${options.host}:${options.port}/viewer.html`);
      return response.ok;
    } catch {
      return false;
    }
  }, 5000);
  if (!ok) {
    throw new Error(`Viewer server started, but viewer.html did not respond. See ${options.log}`);
  }

  const pids = await pidsOnPort(options.port);
  console.log(`Viewer server: http://${options.host}:${options.port}/viewer.html`);
  console.log(`Root: ${options.root}`);
  console.log(`PID: ${pids.join(", ") || child.pid}`);
  console.log(`Log: ${options.log}`);
}

async function pidsOnPort(port) {
  const result = await execFileText("lsof", [`-tiTCP:${port}`, "-sTCP:LISTEN"], { allowFailure: true });
  return result
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value > 0);
}

function execFileText(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile(command, args, (error, stdout, stderr) => {
      if (error && !options.allowFailure) {
        reject(new Error(String(stderr || stdout || error.message).trim()));
        return;
      }
      resolve(String(stdout || ""));
    });
  });
}

async function waitFor(predicate, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await predicate()) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}
