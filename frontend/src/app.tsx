import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/app-shell";
import { SkeletonRows } from "./components/ui/data-state";
import { useAuth } from "./features/auth/auth-context";
import { AuditLogPage } from "./pages/audit-log-page";
import { LoginPage } from "./pages/login-page";
import { LLMSettingsPage } from "./pages/llm-settings-page";
import { TransformationHistoryPage } from "./pages/transformation-history-page";
import { TransformationPage } from "./pages/transformation-page";
import { DocumentMemoryPage } from "./pages/document-memory-page";

function ProtectedLayout() {
  const { user, loading } = useAuth();
  if (loading) return <div className="mx-auto max-w-5xl p-10"><SkeletonRows count={8} /></div>;
  return user ? <AppShell /> : <Navigate to="/login" replace />;
}
function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return user?.role === "ADMIN" ? children : <Navigate to="/transformations" replace />;
}
export function App() {
  return <Routes><Route path="/login" element={<LoginPage />} /><Route element={<ProtectedLayout />}><Route index element={<Navigate to="/transformations" replace />} /><Route path="transformations" element={<TransformationPage />} /><Route path="transformations/:transformationId" element={<TransformationPage />} /><Route path="history" element={<TransformationHistoryPage />} /><Route path="document-memory" element={<DocumentMemoryPage />} /><Route path="llm-settings" element={<AdminOnly><LLMSettingsPage /></AdminOnly>} /><Route path="audit-log" element={<AdminOnly><AuditLogPage /></AdminOnly>} /></Route><Route path="*" element={<Navigate to="/transformations" replace />} /></Routes>;
}
