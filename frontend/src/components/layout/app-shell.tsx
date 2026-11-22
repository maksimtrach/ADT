import { ChevronLeft, ChevronRight, FileClock, FileSpreadsheet, History, LogOut, Menu, Settings, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../../features/auth/auth-context";
import { cn } from "../../lib/cn";
import { Button } from "../ui/button";

const SIDEBAR_KEY = "adt.sidebarCollapsed";

export function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsedState] = useState(() => window.localStorage.getItem(SIDEBAR_KEY) === "true");
  const setCollapsed = (value: boolean) => { setCollapsedState(value); window.localStorage.setItem(SIDEBAR_KEY, String(value)); };
  const nav = [
    { to: "/transformations", label: "Преобразования", icon: FileSpreadsheet },
    { to: "/history", label: "История", icon: History },
    { to: "/document-memory", label: "Память обработки", icon: FileSpreadsheet },
    ...(user?.role === "ADMIN" ? [
      { to: "/llm-settings", label: "Настройки LLM", icon: Settings },
      { to: "/audit-log", label: "Журнал действий", icon: FileClock },
    ] : []),
  ];
  const sidebar = (isCollapsed: boolean) => (
    <aside className={cn("flex h-full flex-col bg-navy text-white transition-[width] duration-200", isCollapsed ? "w-18" : "w-64")}>
      <div className={cn("flex h-16 items-center border-b border-white/10", isCollapsed ? "justify-center px-3" : "gap-3 px-5")}>
        <div className="rounded bg-action p-2"><ShieldCheck className="h-5 w-5" /></div>
        {!isCollapsed && <div><div className="font-display text-sm font-bold">Конструктор</div><div className="text-[10px] uppercase tracking-widest text-slate-400">Audit data transformer</div></div>}
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {nav.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} onClick={() => setMobileOpen(false)} title={isCollapsed ? label : undefined} className={({ isActive }) => cn("flex items-center gap-3 rounded px-3 py-2.5 text-sm font-medium text-slate-300 transition-colors hover:bg-white/10 hover:text-white", isCollapsed && "justify-center px-0", isActive && "bg-action text-white")}>
            <Icon className="h-4 w-4 shrink-0" />{!isCollapsed && label}
          </NavLink>
        ))}
      </nav>
      <div className={cn("border-t border-white/10", isCollapsed ? "p-3" : "p-4")}>
        {!isCollapsed && <div className="mb-3"><div className="text-sm font-semibold">{user?.name}</div><div className="truncate text-xs text-slate-400">{user?.email}</div></div>}
        <button className={cn("flex w-full items-center gap-2 rounded text-xs text-slate-300 hover:text-white", isCollapsed && "justify-center py-2")} onClick={() => void logout().then(() => navigate("/login", { replace: true }))}><LogOut className="h-4 w-4 shrink-0" />{!isCollapsed && "Выйти"}</button>
        <button className="ml-auto mt-3 flex h-9 w-9 items-center justify-center rounded border border-white/15 text-slate-300 hover:bg-white/10 hover:text-white" onClick={() => setCollapsed(!isCollapsed)} title={isCollapsed ? "Развернуть меню" : "Свернуть меню"}>{isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}</button>
      </div>
    </aside>
  );
  return (
    <div className="min-h-screen lg:flex">
      <div className="hidden lg:fixed lg:inset-y-0 lg:block">{sidebar(collapsed)}</div>
      {mobileOpen && <div className="fixed inset-0 z-40 flex bg-slate-950/40 lg:hidden"><div>{sidebar(false)}</div><Button variant="ghost" className="m-3 text-white" onClick={() => setMobileOpen(false)}><X className="h-5 w-5" /></Button></div>}
      <div className={cn("flex min-h-screen min-w-0 flex-1 flex-col transition-[padding] duration-200", collapsed ? "lg:pl-18" : "lg:pl-64")}>
        <header className="sticky top-0 z-20 flex h-16 items-center border-b bg-white px-4 lg:hidden"><Button variant="ghost" onClick={() => setMobileOpen(true)}><Menu className="h-5 w-5" /></Button><span className="ml-3 font-display font-semibold text-navy">Конструктор расшифровок</span></header>
        <main className="mx-auto w-full max-w-[1440px] flex-1 p-4 md:p-6 lg:p-8"><Outlet /></main>
        <footer className="border-t bg-white px-6 py-4 text-center text-xs text-slate-400">Audit Data Transformer · локальная обработка данных</footer>
      </div>
    </div>
  );
}
