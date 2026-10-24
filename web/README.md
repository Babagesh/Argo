# Scrummy web

Next.js (App Router) + Auth.js (credentials) + Prisma/Postgres. Deploys to Vercel.

## Local setup
1. `cp .env.example .env.local` and fill in `DATABASE_URL` (Neon, Vercel Postgres, or local Postgres) and `AUTH_SECRET` (`npx auth secret`).
2. `npm install`
3. `npx prisma db push` (creates the User table)
4. `npm run dev` -> http://localhost:3000

## Deploy to Vercel
1. Import the repo in Vercel and set **Root Directory** to `web`.
2. Add env vars `DATABASE_URL` and `AUTH_SECRET`.
3. Run `npx prisma db push` once against the production `DATABASE_URL`.
