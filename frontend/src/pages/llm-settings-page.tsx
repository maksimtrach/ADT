import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleX, Plus, Save, Server, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { ErrorState, SkeletonRows } from "../components/ui/data-state";
import { Input, Select } from "../components/ui/input";
import { useToast } from "../components/ui/toast";
import { api } from "../lib/api";
import type { LLMConfiguration, LLMProbeResult, LLMProfile, LLMSettingsResponse } from "../lib/types";

const defaultProfile: LLMProfile = {
  enabled: true,
  protocol: "ollama",
  base_url: "http://127.0.0.1:11434",
  model: "",
  timeout_seconds: 180,
  connect_timeout_seconds: 10,
  thinking: false,
  no_think_suffix: true,
  keep_alive: "10m",
  api_key_env: null,
  structured_output: true,
  generation: { temperature: 0, max_tokens: 1800, context_size: 16384 },
};

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="space-y-1.5"><span className="block text-sm font-semibold text-slate-700">{label}</span>{children}{hint && <span className="block text-xs text-slate-500">{hint}</span>}</label>;
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (value: boolean) => void; label: string; hint?: string }) {
  return <label className="flex cursor-pointer gap-3 rounded border bg-slate-50 px-3 py-2.5">
    <input type="checkbox" className="mt-0.5 h-4 w-4 accent-blue-600" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    <span><span className="block text-sm font-semibold text-slate-700">{label}</span>{hint && <span className="mt-0.5 block text-xs text-slate-500">{hint}</span>}</span>
  </label>;
}

function numberValue(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function LLMSettingsPage() {
  const toast = useToast();
  const [draft, setDraft] = useState<LLMConfiguration | null>(null);
  const [override, setOverride] = useState<string | null>(null);
  const [probeResults, setProbeResults] = useState<Record<string, LLMProbeResult>>({});

  const settings = useQuery({
    queryKey: ["llm-settings"],
    queryFn: () => api<LLMSettingsResponse>("/llm-settings"),
  });

  useEffect(() => {
    if (!settings.data) return;
    setDraft(structuredClone(settings.data.configuration));
    setOverride(settings.data.environment_override);
    setProbeResults({});
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (configuration: LLMConfiguration) => api<LLMSettingsResponse>("/llm-settings", {
      method: "PUT",
      body: JSON.stringify(configuration),
    }),
    onSuccess: (result) => {
      setDraft(structuredClone(result.configuration));
      setOverride(result.environment_override);
      toast.show("Настройки LLM сохранены");
    },
    onError: (error: Error) => toast.show(error.message, "error"),
  });

  const probe = useMutation({
    mutationFn: ({ name, profile }: { name: string; profile: LLMProfile }) => api<LLMProbeResult>("/llm-settings/test", {
      method: "POST",
      body: JSON.stringify({ profile_name: name, profile }),
    }),
    onSuccess: (result) => {
      setProbeResults((current) => ({ ...current, [result.profile_name]: result }));
    },
    onError: (error: Error) => toast.show(error.message, "error"),
  });

  function updateProfile(name: string, change: Partial<LLMProfile>) {
    setDraft((current) => current ? {
      ...current,
      profiles: { ...current.profiles, [name]: { ...current.profiles[name], ...change } },
    } : current);
    setProbeResults((current) => {
      const next = { ...current };
      delete next[name];
      return next;
    });
  }

  function updateGeneration(name: string, change: Partial<LLMProfile["generation"]>) {
    if (!draft) return;
    updateProfile(name, { generation: { ...draft.profiles[name].generation, ...change } });
  }

  function addProfile() {
    if (!draft) return;
    let index = Object.keys(draft.profiles).length + 1;
    let name = `profile_${index}`;
    while (draft.profiles[name]) name = `profile_${++index}`;
    setDraft({ ...draft, profiles: { ...draft.profiles, [name]: structuredClone(defaultProfile) } });
  }

  function removeProfile(name: string) {
    if (!draft || Object.keys(draft.profiles).length === 1) return;
    const profiles = { ...draft.profiles };
    delete profiles[name];
    const remaining = Object.keys(profiles);
    setDraft({
      ...draft,
      profiles,
      active_profile: draft.active_profile === name ? remaining[0] : draft.active_profile,
      fallback_profiles: draft.fallback_profiles.filter((item) => item !== name),
    });
    setProbeResults((current) => {
      const next = { ...current };
      delete next[name];
      return next;
    });
  }

  function toggleFallback(name: string, checked: boolean) {
    if (!draft) return;
    setDraft({
      ...draft,
      fallback_profiles: checked
        ? [...draft.fallback_profiles, name]
        : draft.fallback_profiles.filter((item) => item !== name),
    });
  }

  if (settings.isError) return <ErrorState message={settings.error.message} retry={() => void settings.refetch()} />;
  if (settings.isLoading || !draft) return <SkeletonRows count={8} />;

  const profileNames = Object.keys(draft.profiles);
  return <div className="space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="text-xs font-bold uppercase tracking-widest text-action">Администрирование</p>
        <h1 className="mt-1 font-display text-3xl font-bold text-navy">Настройки LLM</h1>
        <p className="mt-1 text-sm text-slate-500">Подключения к локальным и сетевым моделям, порядок переключения и параметры генерации.</p>
      </div>
      <Button loading={save.isPending} onClick={() => save.mutate(draft)}><Save className="h-4 w-4" />Сохранить настройки</Button>
    </header>

    {override && <div className="flex gap-3 rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
      <div><div className="font-semibold">Активный профиль переопределён окружением</div><p className="mt-1">Переменная LLM_PROFILE выбирает профиль «{override}». Изменение основного профиля начнёт действовать после удаления этого переопределения и перезапуска backend.</p></div>
    </div>}

    <Card>
      <CardHeader title="Порядок подключения" description="Сначала используется основной профиль, затем включённые резервные профили в указанном порядке." />
      <div className="grid gap-5 p-5 md:grid-cols-2">
        <Field label="Основной профиль" hint={override ? `Фактически сейчас выбран: ${override}` : undefined}>
          <Select value={draft.active_profile} onChange={(event) => {
            const active = event.target.value;
            setDraft({ ...draft, active_profile: active, fallback_profiles: draft.fallback_profiles.filter((name) => name !== active) });
          }}>{profileNames.map((name) => <option key={name} value={name}>{name}</option>)}</Select>
        </Field>
        <div>
          <div className="text-sm font-semibold text-slate-700">Резервные профили</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {profileNames.filter((name) => name !== draft.active_profile).map((name) => <label key={name} className="flex items-center gap-2 rounded border px-3 py-2 text-sm">
              <input type="checkbox" checked={draft.fallback_profiles.includes(name)} onChange={(event) => toggleFallback(name, event.target.checked)} />{name}
            </label>)}
            {profileNames.length === 1 && <span className="text-sm text-slate-500">Добавьте ещё один профиль.</span>}
          </div>
        </div>
      </div>
    </Card>

    <div className="flex items-center justify-between gap-3">
      <div><h2 className="font-display text-xl font-semibold text-navy">Профили подключения</h2><p className="mt-1 text-sm text-slate-500">Проверка использует значения из формы, даже если они ещё не сохранены.</p></div>
      <Button variant="secondary" onClick={addProfile}><Plus className="h-4 w-4" />Добавить профиль</Button>
    </div>

    {profileNames.map((name) => {
      const profile = draft.profiles[name];
      const result = probeResults[name];
      const isProbing = probe.isPending && probe.variables?.name === name;
      return <Card key={name} className="overflow-hidden">
        <CardHeader title={name} description={name === draft.active_profile ? "Основной профиль" : draft.fallback_profiles.includes(name) ? "Резервный профиль" : "Не входит в цепочку переключения"} action={<div className="flex gap-2">
          <Button size="sm" variant="secondary" loading={isProbing} onClick={() => probe.mutate({ name, profile })}><Server className="h-4 w-4" />Проверить</Button>
          <Button size="sm" variant="ghost" disabled={profileNames.length === 1} title="Удалить профиль" onClick={() => removeProfile(name)}><Trash2 className="h-4 w-4 text-red-600" /></Button>
        </div>} />
        <div className="space-y-5 p-5">
          {result && <div className={`flex flex-wrap items-start gap-3 rounded border p-3 text-sm ${result.available ? "border-emerald-200 bg-emerald-50" : result.server_available ? "border-amber-200 bg-amber-50" : "border-red-200 bg-red-50"}`}>
            {result.available ? <CheckCircle2 className="h-5 w-5 text-emerald-700" /> : <CircleX className={`h-5 w-5 ${result.server_available ? "text-amber-700" : "text-red-700"}`} />}
            <div className="min-w-0 flex-1"><div className="font-semibold">{result.message}</div><div className="mt-1 text-xs text-slate-600">Отклик: {result.latency_ms} мс{result.status_code ? ` · HTTP ${result.status_code}` : ""}</div>
              {result.models.length > 0 && <details className="mt-2"><summary className="cursor-pointer text-xs font-semibold">Модели на сервере: {result.models.length}</summary><p className="mt-1 break-words font-mono text-xs">{result.models.join(", ")}</p></details>}
            </div>
          </div>}

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Протокол">
              <Select value={profile.protocol} onChange={(event) => updateProfile(name, { protocol: event.target.value as LLMProfile["protocol"] })}><option value="ollama">Ollama</option><option value="openai">OpenAI-совместимый</option></Select>
            </Field>
            <Field label="Адрес сервера">
              <Input value={profile.base_url} placeholder={profile.protocol === "ollama" ? "http://127.0.0.1:11434" : "http://host:1234/v1"} onChange={(event) => updateProfile(name, { base_url: event.target.value })} />
            </Field>
            <Field label="Модель">
              <Input value={profile.model} placeholder="qwen/qwen3.5-9b" onChange={(event) => updateProfile(name, { model: event.target.value })} />
            </Field>
            <Field label="Переменная API-ключа" hint="Хранится только имя переменной, не сам секрет.">
              <Input value={profile.api_key_env ?? ""} placeholder="REMOTE_LLM_API_KEY" onChange={(event) => updateProfile(name, { api_key_env: event.target.value || null })} />
            </Field>
            <Field label="Общий таймаут, сек.">
              <Input type="number" min={1} value={profile.timeout_seconds} onChange={(event) => updateProfile(name, { timeout_seconds: numberValue(event.target.value, 180) })} />
            </Field>
            <Field label="Таймаут подключения, сек.">
              <Input type="number" min={1} value={profile.connect_timeout_seconds} onChange={(event) => updateProfile(name, { connect_timeout_seconds: numberValue(event.target.value, 10) })} />
            </Field>
            <Field label="Keep alive" hint="Используется Ollama; пустое значение отключает параметр.">
              <Input value={profile.keep_alive ?? ""} placeholder="10m" onChange={(event) => updateProfile(name, { keep_alive: event.target.value || null })} />
            </Field>
            <Field label="Размер контекста">
              <Input type="number" min={512} value={profile.generation.context_size ?? ""} placeholder="По умолчанию модели" onChange={(event) => updateGeneration(name, { context_size: event.target.value ? numberValue(event.target.value, 16384) : null })} />
            </Field>
            <Field label="Температура">
              <Input type="number" min={0} max={2} step={0.1} value={profile.generation.temperature} onChange={(event) => updateGeneration(name, { temperature: numberValue(event.target.value, 0) })} />
            </Field>
            <Field label="Максимум токенов">
              <Input type="number" min={1} value={profile.generation.max_tokens} onChange={(event) => updateGeneration(name, { max_tokens: numberValue(event.target.value, 1800) })} />
            </Field>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Toggle checked={profile.enabled} onChange={(enabled) => updateProfile(name, { enabled })} label="Профиль включён" hint="Отключённый профиль пропускается при обработке." />
            <Toggle checked={profile.structured_output} onChange={(structured_output) => updateProfile(name, { structured_output })} label="Структурированный ответ" hint="Запрашивать JSON-схему, если сервер поддерживает." />
            <Toggle checked={profile.thinking} onChange={(thinking) => updateProfile(name, { thinking })} label="Thinking" hint="Разрешить режим рассуждений модели." />
            <Toggle checked={profile.no_think_suffix} onChange={(no_think_suffix) => updateProfile(name, { no_think_suffix })} label="No think" hint="Добавлять к запросу инструкцию /no_think." />
          </div>
        </div>
      </Card>;
    })}

    <div className="flex justify-end"><Button loading={save.isPending} onClick={() => save.mutate(draft)}><Save className="h-4 w-4" />Сохранить настройки</Button></div>
  </div>;
}
