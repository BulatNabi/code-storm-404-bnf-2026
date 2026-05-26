// API client for the FinTech Regulatory Radar backend (FastAPI).
// All endpoints live under `${BASE_URL}/api`. CORS is open on the backend.
const BASE_URL = 'http://localhost:8000';

// ── Types ───────────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string;          // always a human-readable string (see request())
  code?: string;           // backend error code, e.g. "INVALID_CREDENTIALS"
  status?: number;         // HTTP status
}


export interface UserOut {
  id: string;
  email: string;
  name: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: UserOut;
}

export interface FileInfo {
  name: string;
  size: number;
  url?: string;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  jira_board_id?: string;
  files: FileInfo[];
  analysis_count: number;
  last_analysis_at?: string;
  created_at: string;
}

export interface JiraBoard {
  id: string;
  board_key: string;
  board_name: string;
  domain: string;
  email: string;
  created_at: string;
}

// Frontend-facing single-connection view, derived from the backend's boards.
export interface JiraStatus {
  connected: boolean;
  domain?: string;
  email?: string;
  connected_at?: string;
  board_key?: string;
  board_name?: string;
}

export interface JiraIssue {
  key: string;
  summary: string;
  status: string;
  issue_type: string;
  assignee?: string;
  updated_at?: string;
  has_attachments: boolean;
}

export interface JiraIssueListResponse {
  items: JiraIssue[];
  total: number;
}

export interface JiraAttachment {
  id: string;
  filename: string;
  size: number;
  mime_type: string;
  url: string;
}

export interface JiraIssueDetail {
  key: string;
  summary: string;
  description: string;
  status: string;
  issue_type: string;
  attachments: JiraAttachment[];
}

// ── Core request helper ───────────────────────────────────────────────────────

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('access_token');
}

interface RequestOpts {
  method?: string;
  body?: unknown;          // plain object → JSON; FormData → multipart (no JSON header)
  auth?: boolean;          // attach Bearer token (default true)
}

async function request<T = any>(path: string, opts: RequestOpts = {}): Promise<T> {
  const { method = 'GET', body, auth = true } = opts;
  const headers: Record<string, string> = {};

  const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';

  if (auth) {
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
    });
  } catch (e) {
    // Network/CORS failure — backend not reachable.
    throw { detail: 'Cannot reach the server. Is the backend running?', status: 0 } as ApiError;
  }

  if (!res.ok) throw await toApiError(res);

  // 204 No Content (logout, delete) or empty body.
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// Normalizes FastAPI errors into a flat { detail: string } the pages can render.
async function toApiError(res: Response): Promise<ApiError> {
  let detail = `Request failed (${res.status})`;
  let code: string | undefined;
  try {
    const body = await res.json();
    const d = body?.detail;
    if (typeof d === 'string') {
      detail = d;
    } else if (d && typeof d === 'object') {
      detail = d.message ?? JSON.stringify(d);
      code = d.code;
    } else if (Array.isArray(body) && body[0]?.msg) {
      detail = body[0].msg;               // pydantic 422 validation array
    }
  } catch {
    /* non-JSON body — keep default */
  }
  return { detail, code, status: res.status };
}

/** Best-effort string extraction from anything thrown by this client. */
export function extractErrorMessage(err: unknown): string {
  if (typeof err === 'string') return err;
  if (err && typeof err === 'object') {
    const e = err as any;
    if (typeof e.detail === 'string') return e.detail;
    if (typeof e.message === 'string') return e.message;
  }
  return 'Something went wrong. Please try again.';
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export function apiRegister(data: {
  name: string;
  surname?: string;       // collected by the form; backend ignores extra fields
  email: string;
  password: string;
}): Promise<AuthResponse> {
  return request('/api/auth/register', { method: 'POST', auth: false, body: data });
}

export function apiLogin(email: string, password: string): Promise<AuthResponse> {
  return request('/api/auth/login', { method: 'POST', auth: false, body: { email, password } });
}

export async function apiLogout(): Promise<void> {
  const refresh_token = (typeof window !== 'undefined' && localStorage.getItem('refresh_token')) || '';
  // Backend requires a refresh_token body and returns 204.
  await request('/api/auth/logout', { method: 'POST', body: { refresh_token } });
}

export function apiRefresh(refresh_token: string): Promise<{ access_token: string }> {
  return request('/api/auth/refresh', { method: 'POST', auth: false, body: { refresh_token } });
}

// ── Projects ──────────────────────────────────────────────────────────────────

// Backend returns { items, total }; the UI expects a plain array.
export async function apiGetProjects(): Promise<Project[]> {
  const data = await request<{ items: Project[]; total: number }>('/api/projects');
  return data.items;
}

// Backend expects multipart/form-data (name, description, jira_board_id, files[]).
export function apiCreateProject(data: {
  name: string;
  description?: string;
  jira_board_id?: string;
  files?: File[];
}): Promise<Project> {
  const form = new FormData();
  form.append('name', data.name);
  if (data.description) form.append('description', data.description);
  if (data.jira_board_id) form.append('jira_board_id', data.jira_board_id);
  (data.files ?? []).forEach(f => form.append('files', f));
  return request('/api/projects', { method: 'POST', body: form });
}

export function apiGetProject(project_id: string): Promise<Project> {
  return request(`/api/projects/${project_id}`);
}

export function apiUpdateProject(
  project_id: string,
  data: { name?: string; description?: string }
): Promise<Project> {
  return request(`/api/projects/${project_id}`, { method: 'PATCH', body: data });
}

export function apiDeleteProject(project_id: string): Promise<void> {
  return request(`/api/projects/${project_id}`, { method: 'DELETE' });
}

// ── Analysis ────────────────────────────────────────────────────────────────

export interface AnalyzeResponse {
  analysis_id: string;
  dashboard: any;
  report?: Record<string, any> | null;
  summary?: string | null;
}

// Backend expects multipart form (text, jira_issue_key) and returns JSON
// (NOT a stream). Files are attached at project creation, not here.
export function apiAnalyze(
  project_id: string,
  data: { text: string; jira_issue_key?: string }
): Promise<AnalyzeResponse> {
  const form = new FormData();
  form.append('text', data.text);
  if (data.jira_issue_key) form.append('jira_issue_key', data.jira_issue_key);
  return request(`/api/projects/${project_id}/analyze`, { method: 'POST', body: form });
}

export function apiGetAnalysisHistory(
  project_id: string,
  limit = 20,
  offset = 0
): Promise<{ items: any[]; total: number }> {
  return request(`/api/projects/${project_id}/history?limit=${limit}&offset=${offset}`);
}

export function apiGetAnalysis(project_id: string, analysis_id: string): Promise<any> {
  return request(`/api/projects/${project_id}/history/${analysis_id}`);
}

// ── Jira ──────────────────────────────────────────────────────────────────────
// The backend models Jira as multiple "boards" (each keyed by a Jira project
// key). The UI treats Jira as one connection, so these adapters operate on the
// user's first registered board.

const JIRA_BASE = '/api/integrations/jira';

// ── TEMPORARY MOCK ───────────────────────────────────────────────────────────
// Forces the Jira integration to appear connected with sample data, bypassing
// the backend (whose /connect path currently 500s on a redirect/JSON bug).
// Flip JIRA_MOCK to false — or delete this block and the `if (JIRA_MOCK)`
// guards below — to restore real API calls.
// TODO: remove once the backend connection bug is fixed.
const JIRA_MOCK = true;

const MOCK_STATUS: JiraStatus = {
  connected: true,
  domain: 'demo-company.atlassian.net',
  email: 'manager.robosoft@gmail.com',
  connected_at: '2026-05-20T09:00:00.000Z',
  board_key: 'BANK',
  board_name: 'Core Banking Platform',
};

const MOCK_BOARDS: JiraBoard[] = [
  { id: '1', board_key: 'BANK', board_name: 'Core Banking Platform', domain: 'demo-company.atlassian.net', email: 'manager.robosoft@gmail.com', created_at: '2026-05-20T09:00:00.000Z' },
  { id: '2', board_key: 'PAY',  board_name: 'Payments & Transfers',  domain: 'demo-company.atlassian.net', email: 'manager.robosoft@gmail.com', created_at: '2026-05-18T09:00:00.000Z' },
  { id: '3', board_key: 'RISK', board_name: 'Risk & Compliance',     domain: 'demo-company.atlassian.net', email: 'manager.robosoft@gmail.com', created_at: '2026-05-15T09:00:00.000Z' },
];

const MOCK_ISSUES: JiraIssue[] = [
  // BANK — Core Banking Platform
  { key: 'BANK-138', summary: 'AML transaction monitoring rule set',        status: 'In Progress',   issue_type: 'Story', assignee: 'Kirill', updated_at: '2026-05-23T11:05:00.000Z', has_attachments: true },
  { key: 'BANK-131', summary: 'PSD2 SCA exemptions audit',                  status: 'To Do',       issue_type: 'Task',  assignee: 'Bulat',  updated_at: '2026-05-22T08:40:00.000Z', has_attachments: false },
  // PAY — Payments & Transfers
  { key: 'PAY-87',  summary: 'SWIFT gpi tracking integration',              status: 'In Progress', issue_type: 'Story', assignee: 'Kirill', updated_at: '2026-05-25T09:10:00.000Z', has_attachments: false },
  { key: 'PAY-83',  summary: 'SEPA instant credit transfer limits',         status: 'To Do',       issue_type: 'Task',  assignee: 'Bulat',  updated_at: '2026-05-21T13:30:00.000Z', has_attachments: true },
  // RISK — Risk & Compliance
  { key: 'RISK-54', summary: 'Basel III capital adequacy report',           status: 'In Review',   issue_type: 'Task',  assignee: 'Eliza',  updated_at: '2026-05-24T17:45:00.000Z', has_attachments: true },
  { key: 'RISK-49', summary: 'GDPR data retention policy update',           status: 'To Do',       issue_type: 'Story', assignee: 'Kirill', updated_at: '2026-05-19T10:00:00.000Z', has_attachments: false },
  { key: 'RISK-45', summary: 'Operational risk scenario modeling',          status: 'Done',        issue_type: 'Task',  assignee: 'Bulat',  updated_at: '2026-05-17T15:20:00.000Z', has_attachments: false },
];
// ─────────────────────────────────────────────────────────────────────────────

export function apiGetJiraBoards(): Promise<JiraBoard[]> {
  if (JIRA_MOCK) return Promise.resolve(MOCK_BOARDS);
  return request(`${JIRA_BASE}/boards`);
}

export async function apiGetJiraStatus(): Promise<JiraStatus> {
  if (JIRA_MOCK) return MOCK_STATUS;
  const boards = await apiGetJiraBoards();
  if (!boards.length) return { connected: false };
  const b = boards[0];
  return {
    connected: true,
    domain: b.domain,
    email: b.email,
    connected_at: b.created_at,
    board_key: b.board_key,
    board_name: b.board_name,
  };
}

// NOTE: the backend requires a Jira project key (board_key) and a display name
// to register a board — it verifies the project exists in Jira. The current
// connect form only collects domain/email/api_token, so board_key/board_name
// must be supplied for this to succeed.
export async function apiConnectJira(data: {
  domain: string;
  email: string;
  api_token: string;
  board_key?: string;
  board_name?: string;
}): Promise<JiraStatus> {
  if (JIRA_MOCK) return MOCK_STATUS;
  if (!data.board_key) {
    throw {
      detail: 'A Jira project key is required to connect (e.g. "BANK").',
      code: 'BOARD_KEY_REQUIRED',
    } as ApiError;
  }
  const board = await request<JiraBoard>(`${JIRA_BASE}/boards/register`, {
    method: 'POST',
    body: {
      board_key: data.board_key,
      board_name: data.board_name || data.board_key,
      domain: data.domain,
      email: data.email,
      api_token: data.api_token,
    },
  });
  return {
    connected: true,
    domain: board.domain,
    email: board.email,
    connected_at: board.created_at,
    board_key: board.board_key,
    board_name: board.board_name,
  };
}

// Removes every registered board (the UI's single "disconnect").
export async function apiDisconnectJira(): Promise<void> {
  if (JIRA_MOCK) return;
  const boards = await apiGetJiraBoards();
  await Promise.all(
    boards.map(b => request(`${JIRA_BASE}/boards/${b.board_key}`, { method: 'DELETE' }))
  );
}


// Lists issues of the first board; `q` filters by key/summary client-side.
export async function apiGetJiraIssues(q?: string): Promise<JiraIssueListResponse> {
  if (JIRA_MOCK) {
    const needle = (q ?? '').toLowerCase();
    const items = needle
      ? MOCK_ISSUES.filter(i => i.key.toLowerCase().includes(needle) || i.summary.toLowerCase().includes(needle))
      : MOCK_ISSUES;
    return { items, total: items.length };
  }
  const boards = await apiGetJiraBoards();
  if (!boards.length) return { items: [], total: 0 };
  const data = await request<JiraIssueListResponse>(
    `${JIRA_BASE}/boards/${boards[0].board_key}/issues`
  );
  if (!q) return data;
  const needle = q.toLowerCase();
  const items = data.items.filter(
    i => i.key.toLowerCase().includes(needle) || i.summary.toLowerCase().includes(needle)
  );
  return { items, total: items.length };
}

// Issues for a SPECIFIC board; `q` filters by key/summary client-side.
export async function apiGetJiraBoardIssues(boardKey: string, q?: string): Promise<JiraIssueListResponse> {
  if (JIRA_MOCK) {
    const needle = (q ?? '').toLowerCase();
    const items = MOCK_ISSUES
      .filter(i => i.key.startsWith(`${boardKey}-`))
      .filter(i => !needle || i.key.toLowerCase().includes(needle) || i.summary.toLowerCase().includes(needle));
    return { items, total: items.length };
  }
  const data = await request<JiraIssueListResponse>(`${JIRA_BASE}/boards/${boardKey}/issues`);
  if (!q) return data;
  const needle = q.toLowerCase();
  const items = data.items.filter(
    i => i.key.toLowerCase().includes(needle) || i.summary.toLowerCase().includes(needle)
  );
  return { items, total: items.length };
}

export async function apiGetJiraIssue(issue_key: string): Promise<JiraIssueDetail> {
  if (JIRA_MOCK) {
    const i = MOCK_ISSUES.find(x => x.key === issue_key) ?? MOCK_ISSUES[0];
    return {
      key: i.key,
      summary: i.summary,
      description: 'Mock issue description for demo purposes.',
      status: i.status,
      issue_type: i.issue_type,
      attachments: [],
    };
  }
  const boards = await apiGetJiraBoards();
  if (!boards.length) throw { detail: 'No Jira board connected.', code: 'NOT_CONNECTED' } as ApiError;
  return request(`${JIRA_BASE}/boards/${boards[0].board_key}/issues/${issue_key}`);
}

// Opens the project backing a Jira issue, creating it on first access.
// The issue key is stored in Project.jira_board_id and used as the dedup key,
// so a second click on the same issue reuses the existing project instead of
// creating a duplicate. Returns the backend project id to navigate to.
export async function apiGetOrCreateProjectForIssue(issue: { key: string; summary: string }): Promise<string> {
  // 1. Already linked to a project?
  const projects = await apiGetProjects();
  const existing = projects.find(p => p.jira_board_id === issue.key);
  if (existing) return existing.id;

  // 2. Not yet — create it. Pull the description from the issue detail (best-effort).
  let description = '';
  try {
    description = (await apiGetJiraIssue(issue.key)).description;
  } catch {
    /* description stays empty if the detail fetch fails */
  }
  const created = await apiCreateProject({
    name: issue.summary,
    description,
    jira_board_id: issue.key,
  });
  return created.id;
}
