import Link from "next/link";

export default function Home() {
  return (
    <main>
      <h1>Scrummy</h1>
      <Link className="btn" href="/login">Log in</Link>
      <Link className="btn" href="/register">Register</Link>
    </main>
  );
}
