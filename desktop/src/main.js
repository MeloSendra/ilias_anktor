import { app, BrowserWindow, dialog, ipcMain } from "electron";
import keytar from "keytar";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { homedir } from "node:os";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const supported = new Set([".html", ".htm", ".md", ".markdown", ".pdf", ".txt"]);
const configFile = join(app.getPath("userData"), "settings.json");
const credentialService = "ILIAS Anktor";
const credentialAccount = "Gemini API Key";

async function filesUnder(root) {
  const result = [];
  async function visit(folder) {
    for (const entry of await readdir(folder, { withFileTypes: true })) {
      const path = join(folder, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (supported.has(entry.name.slice(entry.name.lastIndexOf(".")).toLowerCase())) result.push(path);
    }
  }
  await visit(root);
  return result.sort();
}

async function courses(root) {
  const entries = await readdir(root, { withFileTypes: true });
  return entries.filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name).sort();
}

function createWindow() {
  const window = new BrowserWindow({
    width: 900,
    height: 700,
    autoHideMenuBar: true,
    webPreferences: { preload: join(here, "preload.cjs"), contextIsolation: true, sandbox: true },
  });
  window.loadFile(join(here, "index.html"));
}

async function readSettings() {
  try {
    const settings = JSON.parse(await readFile(configFile, "utf8"));
    return {
      root: settings.root || join(homedir(), "ILIAS"),
      output: settings.output || "ilias-anktor.apkg",
      apiKeyConfigured: Boolean(process.env.GEMINI_API_KEY || await keytar.getPassword(credentialService, credentialAccount)),
      environmentApiKey: Boolean(process.env.GEMINI_API_KEY),
    };
  } catch (error) {
    if (error.code === "ENOENT") {
      return {
        root: join(homedir(), "ILIAS"),
        output: "ilias-anktor.apkg",
        apiKeyConfigured: Boolean(process.env.GEMINI_API_KEY || await keytar.getPassword(credentialService, credentialAccount)),
        environmentApiKey: Boolean(process.env.GEMINI_API_KEY),
      };
    }
    throw new Error(`Could not read settings: ${error.message}`);
  }
}

ipcMain.handle("settings:load", () => readSettings());
ipcMain.handle("settings:save", async (_event, settings) => {
  const apiKey = String(settings.apiKey || "").trim();
  if (apiKey && !process.env.GEMINI_API_KEY) await keytar.setPassword(credentialService, credentialAccount, apiKey);
  const saved = { root: String(settings.root || ""), output: String(settings.output || "ilias-anktor.apkg") };
  await writeFile(configFile, JSON.stringify(saved), "utf8");
  return { root: saved.root, output: saved.output };
});

ipcMain.handle("courses:list", (_event, root) => courses(root));
ipcMain.handle("dialogs:folder", async () => {
  const result = await dialog.showOpenDialog({ properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle("dialogs:output", async () => {
  const result = await dialog.showSaveDialog({ defaultPath: "ilias-anktor.apkg", filters: [{ name: "Anki package", extensions: ["apkg"] }] });
  return result.canceled ? null : result.filePath;
});
ipcMain.handle("estimate", async (_event, { root, selected, mode = "efficient" }) => {
  const files = await filesUnder(root);
  const selectedFiles = files.filter((path) => selected.some((course) => relative(root, path).split("\\")[0] === course));
  const requests = Math.max(1, Math.ceil(selectedFiles.length / 4)) * (mode === "deep" ? 2 : 1);
  return `Estimated Gemini requests: about ${requests}. Actual count depends on extracted text size.`;
});
ipcMain.handle("generate", async (_event, { root, selected, output, mode = "efficient" }) => {
  if (!root || !String(output || "").toLowerCase().endsWith(".apkg")) throw new Error("Choose an ILIAS folder and an .apkg output file.");
  await import("node:fs/promises").then(({ mkdir }) => mkdir(dirname(output), { recursive: true }));
  const files = await filesUnder(root);
  const selectedFiles = files.filter((path) => selected.some((course) => relative(root, path).split("\\")[0] === course));
  if (!selectedFiles.length) throw new Error("Select at least one course with supported files.");
  const apiKey = process.env.GEMINI_API_KEY || await keytar.getPassword(credentialService, credentialAccount);
  if (!apiKey) throw new Error("Set GEMINI_API_KEY or save a Gemini API key in Settings.");
  const args = ["--root", root, "-o", output, "--mode", mode];
  for (const path of selectedFiles) args.push("--file", relative(root, path));
  return new Promise((resolve, reject) => {
    const backend = app.isPackaged
      ? join(process.resourcesPath, "backend", "anki_companion.exe")
      : "python";
    const backendArgs = app.isPackaged ? args : ["-m", "anki_companion", ...args];
    const child = spawn(backend, backendArgs, {
      windowsHide: true,
      env: { ...process.env, GEMINI_API_KEY: apiKey },
    });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => reject(new Error(`Could not start backend: ${error.message}`)));
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      const message = stderr
        .split(/\r?\n/)
        .filter((line) => line && !line.startsWith("Ignoring wrong pointing object") && !line.includes("Multiple definitions in dictionary") && !line.startsWith("usage:") && !line.startsWith("options:"))
        .map((line) => line.replace(/^anki_companion\.exe: error: /, ""))
        .join("\n");
      reject(new Error(message || `Generation failed (${code})`));
    });
  });
});

app.whenReady().then(createWindow);
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
