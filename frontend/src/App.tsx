import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";

import { AskPage } from "./ai/AskPage";
import { ApiError } from "./lib/api";
import { startSession, useSession, useSessionStatus } from "./lib/auth";
import { AuthPage } from "./pages/AuthPage";
import { AiSettingsPage } from "./pages/AiSettingsPage";
import { AuditPage } from "./pages/AuditPage";
import { UpdatePrompt } from "./offline/UpdatePrompt";
import { MembersPage } from "./pages/MembersPage";
import { PageView } from "./pages/PageView";
import { Home, NewWorkspace, WorkspaceHome, WorkspaceLayout } from "./pages/workspace";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Always try the request: offline, the service worker answers from its cache.
      networkMode: "offlineFirst",
      retry: (count, error) => navigator.onLine && !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession();
  const status = useSessionStatus();
  const location = useLocation();
  if (status === "loading") {
    return <div className="p-8 text-sm text-zinc-500" role="status">Loading…</div>;
  }
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
      <Route path="/new-workspace" element={<RequireAuth><NewWorkspace /></RequireAuth>} />
      <Route path="/w/:workspaceId" element={<RequireAuth><WorkspaceLayout /></RequireAuth>}>
        <Route index element={<WorkspaceHome />} />
        <Route path="p/:pageId" element={<PageView />} />
        <Route path="settings" element={<AiSettingsPage />} />
        <Route path="members" element={<MembersPage />} />
        <Route path="activity" element={<AuditPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="ask/:conversationId" element={<AskPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  useEffect(startSession, []);
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
      <UpdatePrompt />
    </QueryClientProvider>
  );
}
