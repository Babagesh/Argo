"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const isRegister = mode === "register";

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const form = new FormData(e.currentTarget);
    const name = String(form.get("name") ?? "");
    const email = String(form.get("email"));
    const password = String(form.get("password"));

    if (isRegister) {
      const res = await fetch("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error ?? "Registration failed");
        setLoading(false);
        return;
      }
    }

    const result = await signIn("credentials", { email, password, redirect: false });
    setLoading(false);
    if (result?.error) {
      setError("Invalid email or password");
      return;
    }
    router.push("/dashboard");
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} className="card">
      <h1>{isRegister ? "Create account" : "Log in"}</h1>
      {isRegister && <input name="name" placeholder="Name (optional)" autoComplete="name" />}
      <input name="email" type="email" placeholder="Email" required autoComplete="email" />
      <input
        name="password"
        type="password"
        placeholder="Password"
        required
        minLength={isRegister ? 8 : undefined}
        autoComplete={isRegister ? "new-password" : "current-password"}
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={loading}>
        {loading ? "..." : isRegister ? "Register" : "Log in"}
      </button>
      <p className="muted">
        {isRegister ? (
          <>Already have an account? <Link href="/login">Log in</Link></>
        ) : (
          <>No account? <Link href="/register">Register</Link></>
        )}
      </p>
    </form>
  );
}
