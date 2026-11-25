import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App.tsx";
import { initializeAppearance } from "./lib/appearance";
import "./index.css";

const stopAppearance = initializeAppearance();
if (import.meta.hot) import.meta.hot.dispose(stopAppearance);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
