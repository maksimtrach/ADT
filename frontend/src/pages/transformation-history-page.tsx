import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RotateCcw } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/data-state";
import { useToast } from "../components/ui/toast";
import { api, apiWithMeta, downloadResult } from "../lib/api";
import type { DataType, TransformationDetail, TransformationSummary } from "../lib/types";

const typeLabels: Record<DataType, string> = {
  TRIAL_BALANCE: "Оборотно-сальдовая ведомость",
  JOURNAL: "Журнал проводок",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

export function TransformationHistoryPage() {
  const [page, setPage] = useState(1);
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey: ["transformations", page],
    queryFn: () => apiWithMeta<TransformationSummary[]>(`/transformations?page=${page}&page_size=20`),
  });
  const pages = Math.max(1, Math.ceil((history.data?.meta?.total ?? 0) / 20));
  const retry = useMutation({
    mutationFn: (id: string) => api<TransformationDetail>(`/transformations/${id}/retry`, {
      method: "POST",
      body: JSON.stringify({ feedback: null }),
    }),
    onSuccess: (item) => {
      void queryClient.invalidateQueries({ queryKey: ["transformations"] });
      toast.show("Повторная обработка запущена");
      navigate(`/transformations/${item.id}`);
    },
    onError: (error) => toast.show(error.message, "error"),
  });
  const download = async (item: TransformationSummary) => {
    try {
      await downloadResult(item.id, `${item.source_filename.replace(/\.xlsx$/i, "")}-result.xlsx`);
    } catch (error) {
      toast.show(error instanceof Error ? error.message : "Не удалось скачать файл", "error");
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-bold uppercase tracking-widest text-action">Архив результатов</p>
        <h1 className="mt-1 font-display text-3xl font-bold text-navy">История преобразований</h1>
        <p className="mt-1 text-sm text-slate-500">Откройте результат, скачайте файл или запустите обработку повторно</p>
      </header>

      <Card className="overflow-hidden">
        <CardHeader title="Все преобразования" description="Результаты сохраняются и доступны после обновления страницы" />
        {history.isLoading ? <SkeletonRows count={8} /> : history.isError ? (
          <div className="p-5"><ErrorState message={history.error.message} retry={() => void history.refetch()} /></div>
        ) : history.data?.data.length ? (
          <>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead><tr><th>Дата и время</th><th>Исходный файл</th><th>Тип данных</th><th>Записей</th><th>Статус</th><th>Контроль</th><th>Действия</th></tr></thead>
                <tbody>
                  {history.data.data.map((item) => (
                    <tr key={item.id}>
                      <td className="whitespace-nowrap font-mono text-xs">{formatDate(item.created_at)}</td>
                      <td className="max-w-72 truncate font-semibold text-navy">{item.source_filename}</td>
                      <td>{typeLabels[item.data_type]}</td>
                      <td className="font-mono">{item.processed_rows}</td>
                      <td><Badge value={item.status} /></td>
                      <td><Badge value={item.control_status} /></td>
                      <td>
                        <div className="flex whitespace-nowrap">
                          <Button variant="ghost" size="sm" onClick={() => navigate(`/transformations/${item.id}`)}>Открыть</Button>
                          {item.version > 0 && <Button variant="ghost" size="sm" onClick={() => void download(item)}><Download className="h-3.5 w-3.5" />Excel</Button>}
                          <Button variant="ghost" size="sm" disabled={item.status === "RUNNING" || item.status === "QUEUED" || retry.isPending} onClick={() => retry.mutate(item.id)}><RotateCcw className="h-3.5 w-3.5" />Повторить</Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t bg-slate-50 px-5 py-3">
              <span className="text-xs text-slate-500">Страница {page} из {pages} · преобразований: {history.data.meta?.total ?? 0}</span>
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Назад</Button>
                <Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>Далее</Button>
              </div>
            </div>
          </>
        ) : <EmptyState title="История пуста" description="Первое преобразование появится здесь после загрузки файла." />}
      </Card>
    </div>
  );
}
