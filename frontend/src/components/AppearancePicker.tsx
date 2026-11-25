import { appearances, setAppearance, useAppearance, type Appearance } from "@/lib/appearance";

/** A native select supports keyboard navigation and the mobile OS picker. */
export function AppearancePicker() {
  const appearance = useAppearance();
  return (
    <label className="inline-flex max-w-full items-center gap-2 rounded-lg border border-zinc-200 bg-white px-2.5 text-sm shadow-xs focus-within:ring-2 focus-within:ring-brand-500 dark:border-zinc-700 dark:bg-zinc-900">
      <svg aria-hidden="true" viewBox="0 0 20 20" className="size-4 shrink-0 text-brand-600 dark:text-brand-500">
        <circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 3a7 7 0 0 1 0 14Z" fill="currentColor" />
      </svg>
      <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">Theme</span>
      <select
        value={appearance}
        onChange={(event) => setAppearance(event.target.value as Appearance)}
        className="min-w-0 cursor-pointer bg-transparent py-2 pr-1 focus:outline-none"
      >
        {(Object.keys(appearances) as Appearance[]).map((key) => (
          <option key={key} value={key}>{appearances[key]}</option>
        ))}
      </select>
    </label>
  );
}
