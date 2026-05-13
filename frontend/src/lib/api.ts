// Base URL: NEXT_PUBLIC_API_BASE override → same host as the frontend
// (works behind any server IP) → localhost fallback for SSR/build.
const BASE_URL =
  (typeof process !== 'undefined' && process.env && process.env.NEXT_PUBLIC_API_BASE)
    || (typeof window !== 'undefined'
          ? `${window.location.protocol}//${window.location.hostname}:8000`
          : 'http://localhost:8000');

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

// Backend returns {items, total}; UI expects a bare array.
export async function apiGetProjects() {
  const res = await fetch(`${BASE_URL}/api/projects`, { headers: authHeaders() });
  if (!res.ok) throw await res.json();
  const data = await res.json();
  return Array.isArray(data) ? data : (data.items || []);
}

// Backend POST /api/projects is multipart Form (it also accepts optional
// PDF/DOCX uploads). DO NOT set Content-Type manually — the browser fills
// the boundary header automatically for FormData bodies.
export async function apiCreateProject(data: { name: string; description?: string }) {
  const fd = new FormData();
  fd.append('name', data.name);
  if (data.description) fd.append('description', data.description);

  const token = getToken();
  const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(`${BASE_URL}/api/projects`, {
    method: 'POST',
    headers,
    body: fd,
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

// Submit a feature description for analysis. Backend POST /api/projects/{id}/analyze
// is multipart Form, returns synchronous JSON {analysis_id, dashboard, report, summary}.
export async function apiAnalyzeFeature(project_id: string, text: string, jira_issue_key?: string) {
  const fd = new FormData();
  fd.append('text', text);
  if (jira_issue_key) fd.append('jira_issue_key', jira_issue_key);

  const token = getToken();
  const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(`${BASE_URL}/api/projects/${project_id}/analyze`, {
    method: 'POST',
    headers,
    body: fd,
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

