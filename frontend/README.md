# FeatureAI — Next.js Frontend

A Next.js 14 frontend connected to the FeatureAI backend at `http://45.130.127.181:8000`.

## Pages

| Route | Description | Auth required |
|---|---|---|
| `/` | Landing / start page | No |
| `/login` | Login with email & password | No |
| `/register` | Register with name, surname, email, password | No |
| `/logout` | Logged-out confirmation page | No |
| `/projects` | Projects dashboard (create / select / integrate Jira) | ✅ Yes |
| `/projects/[project_id]` | Project detail — update & delete | ✅ Yes |

## Setup

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Auth flow

- Tokens are stored in `localStorage` as `access_token` and `refresh_token`.
- Protected pages use `AuthGuard` — redirects to `/login` if no token is present.
- Logout clears tokens and redirects to `/logout`.

## API

All API calls are centralized in `src/lib/api.ts`.  
Base URL: `http://45.130.127.181:8000`

## Project structure

```
src/
  app/
    page.tsx                        # Start page
    login/page.tsx                  # Login
    register/page.tsx               # Register
    logout/page.tsx                 # Logout confirmation
    projects/
      page.tsx                      # Projects dashboard
      [project_id]/page.tsx         # Project detail
    globals.css                     # Design system
    layout.tsx                      # Root layout
  components/
    Navbar.tsx                      # Shared navbar (4 variants)
    AuthGuard.tsx                   # Route protection
  lib/
    api.ts                          # All API calls
    auth-context.tsx                # Auth state context
```
