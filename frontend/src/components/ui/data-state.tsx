import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

export function SkeletonRows({ count = 5 }: { count?: number }) {
  return <div className="space-y-3 p-5">{Array.from({ length: count }, (_, index) => <div key={index} className="h-12 animate-pulse rounded bg-slate-100" />)}</div>;
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <div className="mb-4 rounded-full bg-slate-100 p-3"><Inbox className="h-6 w-6 text-slate-500" /></div>
      <h3 className="font-semibold text-navy">{title}</h3>
      <p className="mt-1 max-w-md text-sm text-slate-500">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="flex items-center gap-3 border border-red-200 bg-red-50 p-4 text-sm text-red-800"><AlertTriangle className="h-5 w-5 shrink-0" /><span>{message}</span>{retry && <button className="ml-auto font-semibold underline" onClick={retry}>Повторить</button>}</div>;
}

