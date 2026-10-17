import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

interface ToastItem { id: number; message: string; tone: "success" | "error" }
const ToastContext = createContext<{ show: (message: string, tone?: ToastItem["tone"]) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const show = useCallback((message: string, tone: ToastItem["tone"] = "success") => {
    const id = Date.now() + Math.random();
    setItems((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 4500);
  }, []);
  const value = useMemo(() => ({ show }), [show]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-5 right-5 z-[70] space-y-2" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`flex min-w-72 max-w-md items-center gap-3 rounded border bg-white px-4 py-3 shadow-popover ${item.tone === "error" ? "border-red-200" : "border-emerald-200"}`}>
            {item.tone === "error" ? <AlertCircle className="h-5 w-5 shrink-0 text-red-600" /> : <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />}
            <span className="text-sm font-medium">{item.message}</span>
            <button className="ml-auto" onClick={() => setItems((current) => current.filter((entry) => entry.id !== item.id))}><X className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() { const context = useContext(ToastContext); if (!context) throw new Error("ToastProvider missing"); return context; }

