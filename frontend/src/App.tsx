import { AppShell } from "./components/AppShell";

export default function App() {
  return (
    <AppShell>
      <div className="mx-auto max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight">Welcome to Ymer</h1>
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
          Your workspace is empty. Documents, databases and mind maps will appear here.
        </p>
      </div>
    </AppShell>
  );
}
