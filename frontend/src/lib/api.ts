// BASE_URL resolution order:
//   1) NEXT_PUBLIC_API_BASE (build-time env, takes priority)
//   2) Same host as the browser, port 8000 (works when frontend is
//      accessed via server IP — localhost in client JS = user's own
//      machine, not the server)
//   3) localhost:8000 (SSR / dev fallback)
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

// Note: backend returns {items: Project[], total: number}. The UI expects a
// bare array, so we unwrap here.
export async function apiGetProjects() {
  const res = await fetch(`${BASE_URL}/api/projects`, { headers: authHeaders() });
  if (!res.ok) throw await res.json();
  const data = await res.json();
  return Array.isArray(data) ? data : (data.items || []);
}

// Backend `POST /api/projects` is multipart (it also accepts optional PDF/DOCX
// uploads). Build FormData explicitly — fetch sets the right boundary header
// when you pass a FormData body, so DO NOT set Content-Type manually.
export async function apiCreateProject(data: {
  name: string;
  description?: string;
  jira_board_id?: string;
  files?: File[];
}) {
  const fd = new FormData();
  fd.append('name', data.name);
  if (data.description) fd.append('description', data.description);
  if (data.jira_board_id) fd.append('jira_board_id', data.jira_board_id);
  for (const f of data.files || []) fd.append('files', f);

  const token = getToken();
  const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(`${BASE_URL}/api/projects`, {
    method: 'POST',
    headers,                           // no Content-Type — browser fills with boundary
    body: fd,
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

// Submit a feature description for analysis. Backend `POST /api/projects/{id}/analyze`
// is also multipart Form (legacy contract — supports `text`, `jira_issue_key`).
export async function apiAnalyzeFeature(
  project_id: string,
  text: string,
  jira_issue_key?: string,
) {
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

// Analysis history for a project.
export async function apiGetAnalysisHistory(project_id: string, limit = 20, offset = 0) {
  const res = await fetch(
    `${BASE_URL}/api/projects/${project_id}/history?limit=${limit}&offset=${offset}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiGetAnalysis(project_id: string, analysis_id: string) {
  const res = await fetch(
    `${BASE_URL}/api/projects/${project_id}/history/${analysis_id}`,
    { headers: authHeaders() },
  );
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

export async function apiGetJiraStatus() {
  const res = await fetch(`${BASE_URL}/api/integrations/jira`, { headers: authHeaders() });
  if (!res.ok) {
    // Backend may return 404 when no integration is configured — surface that
    // as a non-connected status rather than throwing.
    if (res.status === 404) return { connected: false };
    throw await res.json();
  }
  return res.json();
}

export async function apiConnectJira(data: {
  domain: string;
  email: string;
  api_token: string;
}) {
  const res = await fetch(`${BASE_URL}/api/integrations/jira/connect`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

export async function apiDisconnectJira() {
  const res = await fetch(`${BASE_URL}/api/integrations/jira`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

