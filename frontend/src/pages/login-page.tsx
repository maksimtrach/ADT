import { zodResolver } from "@hookform/resolvers/zod";
import { DatabaseZap } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { useAuth } from "../features/auth/auth-context";

const schema = z.object({ email: z.email("Введите корректный email"), password: z.string().min(8, "Минимум 8 символов") });
type FormData = z.infer<typeof schema>;

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<FormData>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });
  if (user) return <Navigate to="/transformations" replace />;
  const submit = handleSubmit(async (data) => { try { setError(""); await login(data.email, data.password); navigate("/transformations"); } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось войти"); } });
  return (
    <div className="grid min-h-screen bg-white lg:grid-cols-[1fr_560px]">
      <section className="hidden bg-navy p-16 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3"><div className="rounded bg-action p-2.5"><DatabaseZap className="h-6 w-6" /></div><span className="font-display text-xl font-bold">Конструктор расшифровок</span></div>
        <div className="max-w-2xl"><p className="mb-5 text-xs font-bold uppercase tracking-[.2em] text-blue-300">Интеллектуальная обработка данных</p><h1 className="font-display text-5xl font-bold leading-tight">Из сложной выгрузки — в проверяемую таблицу.</h1><p className="mt-6 max-w-xl text-lg leading-8 text-slate-300">Структурный анализ, контролируемое преобразование и прозрачная история результата.</p></div>
        <p className="text-xs text-slate-500">Локальная обработка · Контроль источников · Полная история действий</p>
      </section>
      <section className="flex min-h-screen flex-col"><div className="flex flex-1 items-center justify-center p-6"><div className="w-full max-w-sm"><div className="mb-9 lg:hidden"><div className="inline-flex rounded bg-navy p-3 text-white"><DatabaseZap className="h-6 w-6" /></div></div><h2 className="font-display text-3xl font-bold text-navy">Вход в систему</h2><p className="mt-2 text-sm text-slate-500">Используйте локальную учётную запись</p>
        <form className="mt-8 space-y-5" onSubmit={(event) => void submit(event)}><div><label className="field-label" htmlFor="email">Email</label><Input id="email" type="email" autoComplete="email" {...register("email")} />{errors.email && <p className="mt-1 text-xs text-red-600">{errors.email.message}</p>}</div><div><label className="field-label" htmlFor="password">Пароль</label><Input id="password" type="password" autoComplete="current-password" {...register("password")} />{errors.password && <p className="mt-1 text-xs text-red-600">{errors.password.message}</p>}</div>{error && <div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}<Button type="submit" className="w-full" loading={isSubmitting}>Войти</Button></form>
      </div></div><footer className="border-t px-6 py-4 text-center text-xs text-slate-400">Audit Data Transformer</footer></section>
    </div>
  );
}

