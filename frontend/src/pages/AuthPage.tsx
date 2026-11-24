import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router";

import { ApiError } from "@/lib/api";
import { login, register, useSession } from "@/lib/auth";

const input =
  "w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900";

export function AuthPage({ mode }: { mode: "login" | "register" }) {
  const session = useSession();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (session) {
    const from = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={from} replace />;
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name);
    } catch (err) {
      if (err instanceof ApiError && err.errors?.length) {
        setError(err.errors.map((x) => `${x.field}: ${x.message}`).join(". "));
      } else {
        setError(err instanceof Error ? err.message : "Something went wrong.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center justify-center gap-2">
          <img src="/favicon.svg" alt="" className="size-8" />
          <span className="text-xl font-semibold tracking-tight">Ymer</span>
        </div>
        <h1 className="mb-6 text-center text-lg font-semibold">
          {mode === "login" ? "Sign in to your account" : "Create an account"}
        </h1>
        <form onSubmit={submit} className="space-y-4">
          {mode === "register" && (
            <label className="block space-y-1">
              <span className="text-sm font-medium">Name</span>
              <input className={input} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} autoComplete="name" />
            </label>
          )}
          <label className="block space-y-1">
            <span className="text-sm font-medium">Email</span>
            <input className={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">Password</span>
            <input
              className={input}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </label>
          {error && (
            <p className="text-sm text-rose-600" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-md bg-brand-600 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-zinc-500">
          {mode === "login" ? (
            <>
              New to Ymer? <Link className="text-brand-600 hover:underline" to="/register">Create an account</Link>
            </>
          ) : (
            <>
              Already have an account? <Link className="text-brand-600 hover:underline" to="/login">Sign in</Link>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
