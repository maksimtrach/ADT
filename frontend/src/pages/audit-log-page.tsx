import { useQuery } from "@tanstack/react-query";
import { FileClock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/data-state";
import { apiWithMeta } from "../lib/api";
import type { AuditLogItem } from "../lib/types";

const actionLabels: Record<string, string> = {
  LOGIN_SUCCEEDED: "Вход в систему", LOGIN_FAILED: "Неудачная попытка входа", LOGOUT: "Выход из системы",
  TRANSFORMATION_CREATED: "Создано преобразование", TRANSFORMATION_COMPLETED: "Преобразование завершено",
  TRANSFORMATION_FAILED: "Ошибка преобразования", TRANSFORMATION_RETRY_REQUESTED: "Запрошена повторная обработка",
  RESULT_DOWNLOADED: "Скачан результат",
};
function formatDate(value: string) { return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value)); }

export function AuditLogPage() {
  const [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ["audit-log", page], queryFn: () => apiWithMeta<AuditLogItem[]>(`/audit-log?page=${page}&page_size=30`) });
  const pages = Math.max(1, Math.ceil((query.data?.meta?.total ?? 0) / 30));
  return (
    <div className="space-y-6">
      <header><p className="text-xs font-bold uppercase tracking-widest text-action">Администрирование</p><h1 className="mt-1 font-display text-3xl font-bold text-navy">Журнал действий</h1><p className="mt-1 text-sm text-slate-500">События авторизации, преобразования, повторной обработки и скачивания файлов</p></header>
      <Card className="overflow-hidden">
        <CardHeader title="Все действия" description="Журнал доступен только администраторам" action={<div className="rounded bg-blue-50 p-2 text-action"><ShieldCheck className="h-5 w-5" /></div>} />
        {query.isLoading ? <SkeletonRows count={8} /> : query.isError ? <div className="p-5"><ErrorState message={query.error.message} retry={() => void query.refetch()} /></div> : query.data?.data.length ? <><div className="overflow-x-auto"><table className="data-table"><thead><tr><th>Дата и время</th><th>Пользователь</th><th>Действие</th><th>Объект</th><th>Результат</th><th>Детали</th></tr></thead><tbody>{query.data.data.map((item) => <tr key={item.id}><td className="whitespace-nowrap font-mono text-xs">{formatDate(item.created_at)}</td><td>{item.user_name ? <><div className="font-semibold text-navy">{item.user_name}</div><div className="text-xs text-slate-500">{item.user_email}</div></> : <span className="text-slate-400">Не определён</span>}</td><td><div className="flex items-center gap-2"><FileClock className="h-4 w-4 text-slate-400" /><span>{actionLabels[item.action] ?? item.action}</span></div></td><td className="font-mono text-xs text-slate-500">{item.entity_id ? `${item.entity_type ?? ""} · ${item.entity_id.slice(0, 8)}` : "—"}</td><td><Badge value={item.outcome} /></td><td className="max-w-sm text-xs text-slate-500">{Object.keys(item.details).length ? Object.entries(item.details).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") : "—"}</td></tr>)}</tbody></table></div><div className="flex items-center justify-between border-t bg-slate-50 px-5 py-3"><span className="text-xs text-slate-500">Страница {page} из {pages} · событий: {query.data.meta?.total ?? 0}</span><div className="flex gap-2"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Назад</Button><Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>Далее</Button></div></div></> : <EmptyState title="Журнал пуст" description="События появятся после действий пользователей." />}
      </Card>
    </div>
  );
}

