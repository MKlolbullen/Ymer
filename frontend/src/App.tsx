import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";

import { ApiError } from "./lib/api";
import { useSession } from "./lib/auth";
import { AuthPage } from "./pages/AuthPage";
import { PageView } from "./pages/PageView";
import { Home, NewWorkspace, WorkspaceHome, WorkspaceLayout } from "./pages/workspace";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession();
  const location = useLocation();
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
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
