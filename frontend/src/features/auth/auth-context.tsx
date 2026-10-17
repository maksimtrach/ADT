import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { api } from "../../lib/api";
import type { User } from "../../lib/types";

interface AuthValue { user?: User; loading: boolean; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void> }
const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const me = useQuery<User | null>({ queryKey: ["me"], queryFn: () => api<User>("/auth/me"), retry: false });
  const login = async (email: string, password: string) => { const user = await api<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }); client.setQueryData(["me"], user); };
  const logout = async () => { await api<void>("/auth/logout", { method: "POST" }); client.setQueryData<User | null>(["me"], null); client.clear(); };
  return <AuthContext.Provider value={{ ...(me.data ? { user: me.data } : {}), loading: me.isLoading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() { const context = useContext(AuthContext); if (!context) throw new Error("AuthProvider missing"); return context; }

