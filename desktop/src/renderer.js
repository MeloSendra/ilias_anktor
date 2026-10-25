const root = document.querySelector("#root");
const courses = document.querySelector("#courses");
const status = document.querySelector("#status");
const mode = document.querySelector("#mode");
const estimate = document.querySelector("#estimate");

async function loadCourses() {
  courses.replaceChildren();
  try {
    for (const course of await window.anktor.listCourses(root.value)) {
      const label = document.createElement("label");
      label.className = "course";
      label.innerHTML = `<input type="checkbox" value="${course.replaceAll('"', "&quot;")}"> ${course}`;
      courses.append(label);
    }
    status.textContent = "Select one or more courses.";
    updateEstimate();
  } catch (error) {
    status.textContent = error.message;
  }
}

document.querySelector("#reload").addEventListener("click", loadCourses);
async function updateEstimate() {
  const selected = [...courses.querySelectorAll("input:checked")].map((input) => input.value);
  if (!selected.length) {
    estimate.textContent = "";
    return;
  }
  try {
    estimate.textContent = await window.anktor.estimate({ root: root.value, selected, mode: mode.value });
  } catch (error) {
    estimate.textContent = error.message;
  }
}
document.querySelector("#choose-root").addEventListener("click", async () => {
  const selected = await window.anktor.chooseFolder();
  if (selected) {
    root.value = selected;
    await loadCourses();
  }
});
document.querySelector("#choose-output").addEventListener("click", async () => {
  const selected = await window.anktor.chooseOutput();
  if (selected) document.querySelector("#output").value = selected;
});
document.querySelector("#generate").addEventListener("click", async () => {
  const selected = [...courses.querySelectorAll("input:checked")].map((input) => input.value);
  status.textContent = "Generating cards with Gemini…";
  try {
    await window.anktor.generate({ root: root.value, selected, output: document.querySelector("#output").value, mode: mode.value });
    status.textContent = "Package generated successfully.";
  } catch (error) {
    status.textContent = error.message;
  }
});
courses.addEventListener("change", updateEstimate);
mode.addEventListener("change", updateEstimate);

document.querySelector("#save-settings").addEventListener("click", async () => {
  try {
    await window.anktor.saveSettings({ root: root.value, output: document.querySelector("#output").value, apiKey: document.querySelector("#api-key").value });
    status.textContent = "Settings saved.";
  } catch (error) {
    status.textContent = error.message;
  }
});

for (const [button, view, otherButton] of [
  [document.querySelector("#show-generator"), document.querySelector("#generator-view"), document.querySelector("#show-settings")],
  [document.querySelector("#show-settings"), document.querySelector("#settings-view"), document.querySelector("#show-generator")],
]) {
  button.addEventListener("click", () => {
    view.hidden = false;
    otherButton.classList.remove("selected");
    button.classList.add("selected");
    if (view.id === "generator-view") document.querySelector("#settings-view").hidden = true;
    else document.querySelector("#generator-view").hidden = true;
  });
}

window.anktor.loadSettings().then((settings) => {
  root.value = settings.root;
  document.querySelector("#output").value = settings.output;
  document.querySelector("#api-key").placeholder = settings.environmentApiKey
    ? "Using GEMINI_API_KEY from the environment"
    : settings.apiKeyConfigured ? "A saved key is configured; enter a new key to replace it" : "Paste your Gemini API key";
  loadCourses();
}).catch((error) => { status.textContent = error.message; });
