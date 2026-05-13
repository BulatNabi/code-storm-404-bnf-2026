const BASE_URL = 'http://45.130.127.181:8000';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('access_token');
}

function authHeaders(): HeadersInit {
  const token = getToken();
  return token
    ? { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    : { 'Content-Type': 'application/json' };
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function apiRegister(data: {
  name: string;
  surname: string;
  email: string;
  password: string;
}) {
  const res = await fetch(`${BASE_URL}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiLogin(email: string, password: string) {
  const res = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await res.json();
  return res.json(); // { access_token, refresh_token }
}

export async function apiLogout() {
  const res = await fetch(`${BASE_URL}/api/auth/logout`, {
    method: 'POST',
    headers: authHeaders(),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiRefresh(refresh_token: string) {
  const res = await fetch(`${BASE_URL}/api/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token }),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

// ── Projects ──────────────────────────────────────────────────────────────────

// export async function apiGetProjects() {
//   const res = await fetch(`${BASE_URL}/api/projects`, {
//     headers: authHeaders(),
//   });
//   if (!res.ok) throw await res.json();
//   return res.json();
// }
export async function apiGetProjects() {
  return [
    { id: '1', name: 'Mobile App Redesign', description: 'UX overhaul for Q3' },
    { id: '2', name: 'Payment Flow', description: 'Stripe integration' },
  ];
}

export async function apiCreateProject(data: { name: string; description?: string }) {
  const res = await fetch(`${BASE_URL}/api/projects`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiGetProject(project_id: string) {
  const res = await fetch(`${BASE_URL}/api/projects/${project_id}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiUpdateProject(
  project_id: string,
  data: { name?: string; description?: string }
) {
  const res = await fetch(`${BASE_URL}/api/projects/${project_id}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiDeleteProject(project_id: string) {
  const res = await fetch(`${BASE_URL}/api/projects/${project_id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

// ── Jira ──────────────────────────────────────────────────────────────────────

// export async function apiGetJiraStatus() {
//   const res = await fetch(`${BASE_URL}/api/integrations/jira`, {
//     headers: authHeaders(),
//   });
//   if (!res.ok) throw await res.json();
//   return res.json();
// }

export async function apiGetJiraStatus() {
  return { connected: false };
}

export async function apiConnectJira(data: {
  domain: string;
  email: string;
  api_token: string;
}) {
  const res = await fetch(`${BASE_URL}/integrations/jira/connect`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiDisconnectJira() {
  const res = await fetch(`${BASE_URL}/integrations/jira`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

