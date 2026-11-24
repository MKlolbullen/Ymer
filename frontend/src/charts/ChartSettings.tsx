import { inputClass } from "@/components/Popover";
import type { ChartConfig, Property } from "@/lib/types";

import { AGGREGATES, CHART_KINDS, DATE_BUCKETS, groupable, isPie, numeric } from "./config";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      {children}
    </label>
  );
}

type Props = { config: ChartConfig; properties: Property[]; onChange: (config: ChartConfig) => void; disabled?: boolean };

/** The form behind a chart: type, grouping, measure and display options. */
export function ChartSettings({ config, properties, onChange, disabled }: Props) {
  const set = (patch: Partial<ChartConfig>) => onChange({ ...config, ...patch });
  const groups = groupable(properties);
  const numbers = numeric(properties);
  const isDate = (id: string | null) => properties.find((p) => p.id === id)?.type === "date";
  const pie = isPie(config.kind);

  return (
    <fieldset disabled={disabled} className="grid gap-3 p-2 sm:grid-cols-2">
      <Field label="Chart type">
        <select className={inputClass} value={config.kind} onChange={(e) => set({ kind: e.target.value as ChartConfig["kind"] })}>
          {CHART_KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label={pie ? "Slices" : "Group by"}>
        <select className={inputClass} value={config.x ?? ""} onChange={(e) => set({ x: e.target.value || null })}>
          <option value="">Nothing (all rows)</option>
          {groups.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      {isDate(config.x) && (
        <Field label="Dates by">
          <select className={inputClass} value={config.date_bucket} onChange={(e) => set({ date_bucket: e.target.value as ChartConfig["date_bucket"] })}>
            {DATE_BUCKETS.map((b) => (
              <option key={b.value} value={b.value}>
                {b.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Measure">
        <select
          className={inputClass}
          value={config.aggregate}
          onChange={(e) => {
            const aggregate = e.target.value as ChartConfig["aggregate"];
            set({ aggregate, measure: aggregate === "count" ? null : (config.measure ?? numbers[0]?.id ?? null) });
          }}
        >
          {AGGREGATES.filter((a) => a.value === "count" || numbers.length).map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </Field>
      {config.aggregate !== "count" && (
        <Field label="Of">
          <select className={inputClass} value={config.measure ?? ""} onChange={(e) => set({ measure: e.target.value || null })}>
            {numbers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      {!pie && (
        <Field label="Split into series by">
          <select className={inputClass} value={config.split_by ?? ""} onChange={(e) => set({ split_by: e.target.value || null })}>
            <option value="">No split</option>
            {groups
              .filter((p) => p.id !== config.x)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </Field>
      )}
      <Field label="Order">
        <select className={inputClass} value={config.sort} onChange={(e) => set({ sort: e.target.value as ChartConfig["sort"] })}>
          <option value="x">By group</option>
          <option value="value_desc">Largest first</option>
          <option value="value_asc">Smallest first</option>
        </select>
      </Field>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm sm:col-span-2">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" className="accent-brand-600" checked={config.hide_empty} onChange={(e) => set({ hide_empty: e.target.checked })} />
          Hide “No value”
        </label>
        {!pie && config.split_by && (config.kind === "bar" || config.kind === "horizontal_bar") && (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" className="accent-brand-600" checked={config.stacked} onChange={(e) => set({ stacked: e.target.checked })} />
            Stacked
          </label>
        )}
        {!pie && (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" className="accent-brand-600" checked={config.cumulative} onChange={(e) => set({ cumulative: e.target.checked })} />
            Running total
          </label>
        )}
      </div>
    </fieldset>
  );
}
