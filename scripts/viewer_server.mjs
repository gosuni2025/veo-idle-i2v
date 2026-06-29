#!/usr/bin/env node
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { access, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const DEFAULT_PORT = 8767;
const PROJECT_FILE = "viewer-project.json";
const PROJECTS_DIR = "viewer-projects";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
const MIME_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".gif", "image/gif"],
  [".webp", "image/webp"],
  [".svg", "image/svg+xml"],
  [".mp4", "video/mp4"],
]);

const args = parseArgs(process.argv.slice(2));
const projectDir = path.resolve(args.projectDir || process.cwd());
let rootDir = path.resolve(args.root || projectDir);
const host = args.host || "127.0.0.1";
const port = Number(args.port || DEFAULT_PORT);
const fixedProjectsDir = args.projectsDir ? path.resolve(args.projectsDir) : "";

assertLoopbackHost(host);
await assertDirectory(rootDir);

const server = createServer(async (request, response) => {
  try {
    if (!isLoopbackRequest(request)) {
      sendJson(response, 403, { error: "Viewer server only accepts loopback requests" });
      return;
    }
    const url = new URL(request.url || "/", `http://${request.headers.host || `${host}:${port}`}`);
    if (url.pathname === "/api/info" && request.method === "GET") {
      sendJson(response, 200, {
        viewer_root: rootDir,
        project_dir: projectsDirForRoot(),
        default_project: "viewer-project",
      });
      return;
    }
    if (url.pathname === "/api/viewers" && request.method === "GET") {
      await handleViewersList(response);
      return;
    }
    if (url.pathname === "/api/viewer" && request.method === "POST") {
      await handleViewerSwitch(request, response);
      return;
    }
    if (url.pathname === "/api/export-dirs" && request.method === "GET") {
      await handleExportDirs(response);
      return;
    }
    if (url.pathname === "/api/projects" && request.method === "GET") {
      await handleProjectsList(response);
      return;
    }
    if (url.pathname === "/api/project" && request.method === "GET") {
      await handleProjectGet(url, response);
      return;
    }
    if (url.pathname === "/api/project" && (request.method === "POST" || request.method === "PUT")) {
      await handleProjectSave(url, request, response);
      return;
    }
    if (url.pathname === "/api/export" && request.method === "POST") {
      await handleExport(request, response);
      return;
    }
    if (url.pathname === "/api/reveal" && request.method === "POST") {
      await handleReveal(request, response);
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      sendJson(response, 404, { error: "Not found" });
      return;
    }
    await serveStatic(url.pathname, response);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Server error";
    sendJson(response, 500, { error: message });
  }
});

server.listen(port, host, () => {
  console.log(`Viewer server: http://${host}:${port}/viewer.html`);
  console.log(`Root: ${rootDir}`);
  console.log(`Projects: ${projectsDirForRoot()}`);
});

function parseArgs(values) {
  const parsed = {};
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === "--port") {
      parsed.port = values[index + 1];
      index += 1;
    } else if (value === "--host") {
      parsed.host = values[index + 1];
      index += 1;
    } else if (value === "--root") {
      parsed.root = values[index + 1];
      index += 1;
    } else if (value === "--projects-dir") {
      parsed.projectsDir = values[index + 1];
      index += 1;
    } else if (value === "--project-dir") {
      parsed.projectDir = values[index + 1];
      index += 1;
    } else if (!parsed.root) {
      parsed.root = value;
    }
  }
  return parsed;
}

function assertLoopbackHost(value) {
  if (!LOOPBACK_HOSTS.has(String(value))) {
    throw new Error(`Viewer server is loopback-only. Unsupported host: ${value}`);
  }
}

function isLoopbackRequest(request) {
  const hostHeader = request.headers.host;
  if (hostHeader && !isLoopbackHostHeader(hostHeader)) {
    return false;
  }

  const originHeader = request.headers.origin;
  if (originHeader && !isLoopbackOrigin(originHeader)) {
    return false;
  }

  return true;
}

function isLoopbackHostHeader(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return true;
  }
  try {
    return LOOPBACK_HOSTS.has(new URL(`http://${raw}`).hostname);
  } catch {
    return false;
  }
}

function isLoopbackOrigin(value) {
  try {
    const origin = new URL(String(value));
    return LOOPBACK_HOSTS.has(origin.hostname);
  } catch {
    return false;
  }
}

async function assertDirectory(value) {
  const info = await stat(value);
  if (!info.isDirectory()) {
    throw new Error(`Viewer root is not a directory: ${value}`);
  }
}

function projectsDirForRoot() {
  return fixedProjectsDir || path.join(rootDir, PROJECTS_DIR);
}

function legacyProjectPathForRoot() {
  return path.join(rootDir, PROJECT_FILE);
}

async function handleViewersList(response) {
  const viewers = await listViewerCandidates();
  sendJson(response, 200, {
    project_dir: projectDir,
    current_root: rootDir,
    viewers,
  });
}

async function handleViewerSwitch(request, response) {
  const body = await readBody(request, 1024 * 1024);
  let data;
  try {
    data = JSON.parse(body);
  } catch (error) {
    sendJson(response, 400, { error: "Request body must be valid JSON" });
    return;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    sendJson(response, 400, { error: "Viewer switch request must be a JSON object" });
    return;
  }

  const requested = String(data.root || data.viewer_root || data.name || "").trim();
  if (!requested) {
    sendJson(response, 400, { error: "Viewer folder is required" });
    return;
  }

  const viewers = await listViewerCandidates();
  const absoluteRequest = path.isAbsolute(requested) ? path.resolve(requested) : path.resolve(projectDir, requested);
  const match = viewers.find((viewer) => (
    viewer.root === absoluteRequest ||
    viewer.relative === requested ||
    viewer.name === requested
  ));
  if (!match) {
    sendJson(response, 404, { error: "Viewer folder is not available" });
    return;
  }

  rootDir = match.root;
  await assertDirectory(rootDir);
  sendJson(response, 200, {
    ok: true,
    viewer_root: rootDir,
    project_dir: projectsDirForRoot(),
    url: "/viewer.html",
  });
}

async function handleExportDirs(response) {
  const exportRoot = path.join(rootDir, "exports");
  const dirs = [];
  dirs.push({
    name: "viewer-project",
    value: "exports/viewer-project",
    path: path.join(exportRoot, "viewer-project"),
    exists: await pathExists(path.join(exportRoot, "viewer-project")),
  });

  try {
    const entries = await readdir(exportRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }
      const dirPath = path.join(exportRoot, entry.name);
      if (dirs.some((item) => item.path === dirPath)) {
        continue;
      }
      const info = await stat(dirPath);
      dirs.push({
        name: entry.name,
        value: path.join("exports", entry.name),
        path: dirPath,
        exists: true,
        updated_at: info.mtime.toISOString(),
      });
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }

  dirs.sort((a, b) => {
    if (a.name === "viewer-project") return -1;
    if (b.name === "viewer-project") return 1;
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  });
  sendJson(response, 200, {
    viewer_root: rootDir,
    export_root: exportRoot,
    dirs,
  });
}

async function listViewerCandidates() {
  const candidates = [];
  const seen = new Set();

  async function addViewer(root) {
    const resolved = path.resolve(root);
    if (seen.has(resolved)) {
      return;
    }
    try {
      const viewerPath = path.join(resolved, "viewer.html");
      const viewerInfo = await stat(viewerPath);
      seen.add(resolved);
      const frameSet = await detectFrameSet(resolved);
      candidates.push({
        name: path.basename(resolved),
        root: resolved,
        relative: path.relative(projectDir, resolved),
        frame_dir: frameSet ? frameSet.name : "",
        frame_count: frameSet ? frameSet.frame_count : 0,
        current: resolved === rootDir,
        updated_at: viewerInfo.mtime.toISOString(),
      });
    } catch (error) {
      if (!error || error.code !== "ENOENT") {
        throw error;
      }
    }
  }

  await addViewer(rootDir);

  const spritesRoot = path.join(projectDir, "outputs/sprites-pymatting");
  try {
    const entries = await readdir(spritesRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        await addViewer(path.join(spritesRoot, entry.name));
      }
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }

  candidates.sort((a, b) => {
    if (a.current) return -1;
    if (b.current) return 1;
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  });
  return candidates;
}

async function detectFrameSet(viewerRoot) {
  const preferred = ["frames_pymatting_fg", "frames_chromakey_alpha", "frames_raw"];
  for (const name of preferred) {
    const frameDir = path.join(viewerRoot, name);
    const frameCount = await countPngFiles(frameDir);
    if (frameCount > 0) {
      return { name, path: frameDir, frame_count: frameCount };
    }
  }

  try {
    const entries = await readdir(viewerRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || !entry.name.startsWith("frames")) {
        continue;
      }
      const frameDir = path.join(viewerRoot, entry.name);
      const frameCount = await countPngFiles(frameDir);
      if (frameCount > 0) {
        return { name: entry.name, path: frameDir, frame_count: frameCount };
      }
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }

  return null;
}

async function countPngFiles(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries.filter((entry) => entry.isFile() && path.extname(entry.name).toLowerCase() === ".png").length;
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return 0;
    }
    throw error;
  }
}

async function pathExists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

async function handleProjectsList(response) {
  const projects = [];
  const projectsDir = projectsDirForRoot();
  const legacyProjectPath = legacyProjectPathForRoot();
  try {
    const entries = await readdir(projectsDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile() || path.extname(entry.name).toLowerCase() !== ".json") {
        continue;
      }
      const filePath = path.join(projectsDir, entry.name);
      const info = await stat(filePath);
      projects.push({
        name: path.basename(entry.name, ".json"),
        file: path.join(PROJECTS_DIR, entry.name),
        size: info.size,
        updated_at: info.mtime.toISOString(),
      });
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }

  try {
    const info = await stat(legacyProjectPath);
    if (!projects.some((project) => project.name === "viewer-project")) {
      projects.push({
        name: "viewer-project",
        file: PROJECT_FILE,
        size: info.size,
        updated_at: info.mtime.toISOString(),
        legacy: true,
      });
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }

  projects.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  sendJson(response, 200, {
    viewer_root: rootDir,
    project_dir: projectsDir,
    projects,
  });
}

async function handleProjectGet(url, response) {
  const name = url.searchParams.get("name");
  const projectPath = projectPathForName(name);
  const legacyProjectPath = legacyProjectPathForRoot();
  try {
    const text = await readFile(projectPath, "utf8");
    send(response, 200, text, "application/json; charset=utf-8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      if (!name) {
        try {
          const text = await readFile(legacyProjectPath, "utf8");
          send(response, 200, text, "application/json; charset=utf-8");
          return;
        } catch (legacyError) {
          if (!legacyError || legacyError.code !== "ENOENT") {
            throw legacyError;
          }
        }
      }
      sendJson(response, 404, { error: "No saved project" });
      return;
    }
    throw error;
  }
}

async function handleProjectSave(url, request, response) {
  const body = await readBody(request, 8 * 1024 * 1024);
  let data;
  try {
    data = JSON.parse(body);
  } catch (error) {
    sendJson(response, 400, { error: "Request body must be valid JSON" });
    return;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    sendJson(response, 400, { error: "Project must be a JSON object" });
    return;
  }

  const projectName = sanitizeProjectName(url.searchParams.get("name") || data.project_name || data.name || "viewer-project");
  const projectPath = projectPathForName(projectName);
  const projectsDir = projectsDirForRoot();
  data.saved_at_server = new Date().toISOString();
  data.project_name = projectName;
  await mkdir(projectsDir, { recursive: true });
  await writeFile(projectPath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  sendJson(response, 200, {
    ok: true,
    name: projectName,
    file: path.join(PROJECTS_DIR, `${projectName}.json`),
    saved_at: data.saved_at_server,
    project_dir: projectsDir,
  });
}

async function handleExport(request, response) {
  const body = await readBody(request, 16 * 1024 * 1024);
  let data;
  try {
    data = JSON.parse(body);
  } catch (error) {
    sendJson(response, 400, { error: "Request body must be valid JSON" });
    return;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    sendJson(response, 400, { error: "Export request must be a JSON object" });
    return;
  }

  const exportDir = resolveExportDir(data.export_dir || data.out_dir);
  const cropConfig = data.export || data.crop_config || data;
  if (!cropConfig.crop || !cropConfig.timeline) {
    sendJson(response, 400, { error: "Export request must include crop and timeline data" });
    return;
  }

  const tempDir = await mkdtemp(path.join(tmpdir(), "veo-viewer-export-"));
  const cropJsonPath = path.join(tempDir, "export-settings.json");
  await writeFile(cropJsonPath, `${JSON.stringify(cropConfig, null, 2)}\n`, "utf8");

  try {
    const args = [
      "scripts/crop_sprite_frames.py",
      rootDir,
      "--crop-json",
      cropJsonPath,
      "--out-dir",
      exportDir,
    ];
    if (data.overwrite === true) {
      args.push("--overwrite");
    }
    const result = await runProcess("python3", args);
    const metadataPath = path.join(exportDir, "crop-meta.json");
    const sequencePath = path.join(exportDir, "animation-sequence.json");
    const metadata = JSON.parse(await readFile(metadataPath, "utf8"));
    sendJson(response, 200, {
      ok: true,
      out_dir: exportDir,
      metadata: metadataPath,
      sequence: sequencePath,
      frame_count: metadata.frame_count,
      unique_frame_count: metadata.unique_frame_count,
      stdout: result.stdout,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Export failed";
    sendJson(response, 500, { error: message });
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

async function handleReveal(request, response) {
  const body = await readBody(request, 1024 * 1024);
  let data;
  try {
    data = JSON.parse(body);
  } catch (error) {
    sendJson(response, 400, { error: "Request body must be valid JSON" });
    return;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    sendJson(response, 400, { error: "Reveal request must be a JSON object" });
    return;
  }

  const exportDir = resolveExportDir(data.export_dir || data.out_dir);
  await mkdir(exportDir, { recursive: true });
  try {
    await runProcess("open", [exportDir]);
    sendJson(response, 200, {
      ok: true,
      out_dir: exportDir,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Open Finder failed";
    sendJson(response, 500, { error: message });
  }
}

function resolveExportDir(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    throw new Error("Export folder is required");
  }
  const resolved = path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(rootDir, raw);
  return resolved;
}

function runProcess(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: projectDir,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
      } else {
        reject(new Error((stderr || stdout || `${command} exited with ${code}`).trim()));
      }
    });
  });
}

function projectPathForName(value) {
  return path.join(projectsDirForRoot(), `${sanitizeProjectName(value || "viewer-project")}.json`);
}

function sanitizeProjectName(value) {
  const raw = String(value || "viewer-project").trim().replace(/\.json$/i, "");
  const cleaned = raw
    .replace(/[\/\\?%*:|"<>]/g, "_")
    .replace(/\s+/g, " ")
    .slice(0, 96)
    .trim();
  return cleaned || "viewer-project";
}

function readBody(request, maxBytes) {
  return new Promise((resolve, reject) => {
    let body = "";
    let bytes = 0;
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      bytes += Buffer.byteLength(chunk);
      if (bytes > maxBytes) {
        reject(new Error("Request body is too large"));
        request.destroy();
        return;
      }
      body += chunk;
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

async function serveStatic(urlPath, response) {
  const decoded = decodeURIComponent(urlPath);
  const relativePath = decoded === "/" ? "viewer.html" : decoded.replace(/^\/+/, "");
  const filePath = path.resolve(rootDir, relativePath);
  if (!isInside(rootDir, filePath)) {
    sendJson(response, 403, { error: "Forbidden" });
    return;
  }

  let info;
  try {
    info = await stat(filePath);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      sendJson(response, 404, { error: "Not found" });
      return;
    }
    throw error;
  }
  if (info.isDirectory()) {
    const indexPath = path.join(filePath, "viewer.html");
    await access(indexPath);
    streamFile(indexPath, response);
    return;
  }
  streamFile(filePath, response);
}

function streamFile(filePath, response) {
  const contentType = MIME_TYPES.get(path.extname(filePath).toLowerCase()) || "application/octet-stream";
  response.writeHead(200, {
    "Content-Type": contentType,
    "Cache-Control": "no-cache",
  });
  createReadStream(filePath).pipe(response);
}

function sendJson(response, statusCode, data) {
  send(response, statusCode, `${JSON.stringify(data, null, 2)}\n`, "application/json; charset=utf-8");
}

function send(response, statusCode, body, contentType) {
  response.writeHead(statusCode, {
    "Content-Type": contentType,
    "Cache-Control": "no-cache",
  });
  response.end(body);
}

function isInside(root, child) {
  const relative = path.relative(root, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}
