const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("anktor", {
  loadSettings: () => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings) => ipcRenderer.invoke("settings:save", settings),
  listCourses: (root) => ipcRenderer.invoke("courses:list", root),
  chooseFolder: () => ipcRenderer.invoke("dialogs:folder"),
  chooseOutput: () => ipcRenderer.invoke("dialogs:output"),
  estimate: (request) => ipcRenderer.invoke("estimate", request),
  generate: (request) => ipcRenderer.invoke("generate", request),
});
