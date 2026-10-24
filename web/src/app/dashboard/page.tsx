import { auth } from "@/auth";
import SignOutButton from "@/components/SignOutButton";

export default async function Dashboard() {
  const session = await auth();
  return (
    <main>
      <h1>Dashboard</h1>
      <p>Signed in as {session?.user?.name ?? session?.user?.email}</p>
      <SignOutButton />
    </main>
  );
}
