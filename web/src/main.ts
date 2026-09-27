import "./style.css";
import { mount } from "./app";

const root = document.getElementById("app");
if (!root) {
  throw new Error("#app root element is missing from index.html");
}
mount(root);
