import "./style.css";
import { mount } from "./app";
import { applyStoredTheme, createThemeToggle } from "./theme";

// Applied before anything else mounts, so a previously chosen theme takes
// effect without a flash of the wrong one.
applyStoredTheme();

const root = document.getElementById("app");
if (!root) {
  throw new Error("#app root element is missing from index.html");
}
mount(root);

const themeToggleRoot = document.getElementById("pw-theme-toggle");
if (!themeToggleRoot) {
  throw new Error("#pw-theme-toggle root element is missing from index.html");
}
themeToggleRoot.appendChild(createThemeToggle());
