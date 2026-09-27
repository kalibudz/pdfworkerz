import React from "react";
import { createRoot } from "react-dom/client";
import FeatureTracker from "./FeatureTracker.jsx";
import "./tracker.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <FeatureTracker />
  </React.StrictMode>,
);
