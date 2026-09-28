// DD-41 faces (docs/DESIGN.md 2.2). Bundled into dist/, never fetched at runtime (FR-P1).
// The two display/UI faces are variable, so one import each covers every weight the design
// uses; DM Mono is static, so 400 and 500 are imported by weight.
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/figtree";
import "@fontsource/dm-mono/400.css";
import "@fontsource/dm-mono/500.css";
import "./index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { createQueryClient } from "./app/queryClient";
import { AuthProvider } from "./auth/AuthProvider";
import { applyTheme, readThemeChoice } from "./ui/theme";

// Before the first paint: a stored choice must not arrive after the page has already drawn
// itself in the other theme.
applyTheme(readThemeChoice());

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

const queryClient = createQueryClient();

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
