'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import Navbar from '@/components/Navbar';
import AuthGuard from '@/components/AuthGuard';
import { useLang } from '@/lib/lang-context';
import {
  apiGetJiraStatus,
  apiConnectJira,
  apiDisconnectJira,
  apiGetJiraIssues,
  extractErrorMessage,
  type JiraStatus,
  type JiraIssue,
} from '@/lib/api';

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  'To Do':       { bg: 'rgba(100,100,120,0.15)', color: '#9090b0' },
  'In Progress': { bg: 'rgba(108,99,255,0.15)',  color: 'var(--accent)' },
  'Done':        { bg: 'rgba(52,211,153,0.15)',  color: 'var(--success)' },
  'In Review':   { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  'Blocked':     { bg: 'rgba(255,77,106,0.15)',  color: 'var(--danger)' },
};

function statusStyle(s?: string) {
  return STATUS_COLORS[s ?? ''] ?? { bg: 'rgba(100,100,120,0.12)', color: 'var(--text-muted)' };
}

function formatDate(iso?: string) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

export default function JiraIntegrationPage() {
  return (
    <AuthGuard>
      <JiraIntegrationContent />
    </AuthGuard>
  );
}

function JiraIntegrationContent() {
  const { t } = useLang();

  // ── Status ────────────────────────────────────────────────────────────────
  const [status, setStatus] = useState<JiraStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  // ── Connect form ──────────────────────────────────────────────────────────
  const [connectForm, setConnectForm] = useState({ domain: '', email: '', api_token: '' });
  const [connecting, setConnecting] = useState(false);
  const [connectMsg, setConnectMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // ── Disconnect ────────────────────────────────────────────────────────────
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectMsg, setDisconnectMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // ── Issues ────────────────────────────────────────────────────────────────
  const [issues, setIssues] = useState<JiraIssue[]>([]);
  const [issuesTotal, setIssuesTotal] = useState(0);
  const [issuesLoading, setIssuesLoading] = useState(false);
  const [issuesError, setIssuesError] = useState('');
  const [search, setSearch] = useState('');

  // ── Load status ───────────────────────────────────────────────────────────
  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const data = await apiGetJiraStatus();
      setStatus(data);
    } catch {
      setStatus({ connected: false });
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  // ── Load issues when connected ────────────────────────────────────────────
  const loadIssues = useCallback(async (q?: string) => {
    setIssuesLoading(true);
    setIssuesError('');
    try {
      const data = await apiGetJiraIssues(q);
      setIssues(data.items ?? []);
      setIssuesTotal(data.total ?? 0);
    } catch (err) {
      setIssuesError(extractErrorMessage(err));
    } finally {
      setIssuesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (status?.connected) loadIssues();
  }, [status?.connected, loadIssues]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    setConnecting(true);
    setConnectMsg(null);
    try {
      const result = await apiConnectJira(connectForm);
      setStatus(result);
      setConnectMsg({ type: 'success', text: t('jira_status.success_connect') });
      setConnectForm({ domain: '', email: '', api_token: '' });
      loadIssues();
    } catch (err) {
      setConnectMsg({ type: 'error', text: extractErrorMessage(err) });
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    setDisconnecting(true);
    setDisconnectMsg(null);
    try {
      await apiDisconnectJira();
      setStatus({ connected: false });
      setIssues([]);
      setIssuesTotal(0);
      setDisconnectMsg({ type: 'success', text: t('jira_status.success_disconnect') });
    } catch (err) {
      setDisconnectMsg({ type: 'error', text: extractErrorMessage(err) });
    } finally {
      setDisconnecting(false);
    }
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    loadIssues(search);
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="page">
      <Navbar variant="app" />
      <div style={{ position: 'relative', zIndex: 1, padding: '48px 0 80px' }}>
        <div className="container" style={{ maxWidth: 1000 }}>

          {/* Back + header */}
          <div style={{ marginBottom: 32 }}>
            <Link href="/projects" className="btn btn-ghost" style={{ marginBottom: 20, display: 'inline-flex' }}>
              ← {t('nav.back_to_projects')}
            </Link>
            <h1 style={{ fontSize: '2.2rem', marginBottom: 8 }}>{t('jira_status.title')}</h1>
            <p style={{ color: 'var(--text-muted)' }}>{t('jira_status.subtitle')}</p>
          </div>

          {/* ── Status badge ───────────────────────────────────────────── */}
          <div style={{ marginBottom: 32 }}>
            {statusLoading ? (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 16px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 100, fontSize: '0.875rem', color: 'var(--text-muted)' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--text-dim)', display: 'inline-block' }} />
                Checking status…
              </div>
            ) : status?.connected ? (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 12, padding: '10px 18px', background: 'var(--success-dim)', border: '1px solid rgba(52,211,153,0.3)', borderRadius: 100 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--success)', display: 'inline-block' }} />
                <span style={{ color: 'var(--success)', fontWeight: 500, fontSize: '0.875rem' }}>
                  Connected
                </span>
                {status.domain && (
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', fontFamily: 'monospace' }}>
                    {status.domain}
                  </span>
                )}
                {status.email && (
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>· {status.email}</span>
                )}
                {status.connected_at && (
                  <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>since {formatDate(status.connected_at)}</span>
                )}
              </div>
            ) : (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '10px 18px', background: 'var(--danger-dim)', border: '1px solid rgba(255,77,106,0.25)', borderRadius: 100 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--danger)', display: 'inline-block' }} />
                <span style={{ color: 'var(--danger)', fontWeight: 500, fontSize: '0.875rem' }}>Not connected</span>
              </div>
            )}
          </div>

          {/* ── Two cards: Connect + Disconnect ────────────────────────── */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24, marginBottom: 40 }}>

            {/* Connect card */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>🔗</span>
                  <h2 style={{ fontSize: '1.1rem' }}>{t('jira_status.connect_title')}</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('jira_status.connect_subtitle')}</p>
              </div>

              <div className="divider" style={{ margin: 0 }} />

              {connectMsg && (
                <div className={`alert alert-${connectMsg.type === 'success' ? 'success' : 'error'}`}>
                  {connectMsg.text}
                </div>
              )}

              <form onSubmit={handleConnect} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div className="form-group">
                  <label className="form-label">{t('jira_status.domain')}</label>
                  <input
                    className="form-input"
                    type="text"
                    placeholder={t('jira_status.domain_placeholder')}
                    value={connectForm.domain}
                    onChange={e => setConnectForm(p => ({ ...p, domain: e.target.value }))}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">{t('jira_status.email')}</label>
                  <input
                    className="form-input"
                    type="email"
                    placeholder={t('jira_status.email_placeholder')}
                    value={connectForm.email}
                    onChange={e => setConnectForm(p => ({ ...p, email: e.target.value }))}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">{t('jira_status.token')}</label>
                  <input
                    className="form-input"
                    type="password"
                    placeholder={t('jira_status.token_placeholder')}
                    value={connectForm.api_token}
                    onChange={e => setConnectForm(p => ({ ...p, api_token: e.target.value }))}
                    required
                  />
                  <p style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: 4 }}>
                    {t('jira_status.token_hint')}
                  </p>
                </div>
                <button type="submit" className="btn btn-primary" disabled={connecting}>
                  {connecting ? t('jira_status.connecting') : t('jira_status.connect')}
                </button>
              </form>
            </div>

            {/* Disconnect card */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20, borderColor: 'rgba(255,77,106,0.2)' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--danger-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>🔌</span>
                  <h2 style={{ fontSize: '1.1rem', color: 'var(--danger)' }}>{t('jira_status.disconnect')}</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
                  Remove the Jira connection for your account. You can reconnect at any time.
                </p>
              </div>

              <div className="divider" style={{ margin: 0 }} />

              {disconnectMsg && (
                <div className={`alert alert-${disconnectMsg.type === 'success' ? 'success' : 'error'}`}>
                  {disconnectMsg.text}
                </div>
              )}

              <div style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                gap: 16,
              }}>
                {/* Current connection info */}
                {status?.connected ? (
                  <div style={{ padding: '14px 16px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {status.domain && (
                      <div style={{ fontSize: '0.85rem' }}>
                        <span style={{ color: 'var(--text-muted)' }}>Domain: </span>
                        <span style={{ fontFamily: 'monospace', color: 'var(--accent)' }}>{status.domain}</span>
                      </div>
                    )}
                    {status.email && (
                      <div style={{ fontSize: '0.85rem' }}>
                        <span style={{ color: 'var(--text-muted)' }}>Email: </span>
                        <span>{status.email}</span>
                      </div>
                    )}
                    {status.connected_at && (
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>
                        Connected since {formatDate(status.connected_at)}
                      </div>
                    )}
                  </div>
                ) : (
                  <div style={{ padding: '14px 16px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', fontSize: '0.875rem', color: 'var(--text-dim)', fontStyle: 'italic' }}>
                    No active connection to disconnect.
                  </div>
                )}

                <button
                  className="btn btn-danger"
                  onClick={handleDisconnect}
                  disabled={disconnecting || !status?.connected}
                >
                  {disconnecting ? t('jira_status.disconnecting') : t('jira_status.disconnect')}
                </button>
              </div>
            </div>
          </div>

          {/* ── Issues list (only when connected) ──────────────────────── */}
          {status?.connected && (
            <div>
              {/* Issues header + search */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16, marginBottom: 20 }}>
                <div>
                  <h2 style={{ fontSize: '1.4rem', marginBottom: 4 }}>{t('jira_issues.title')}</h2>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
                    {!issuesLoading && `${issuesTotal} total`}
                  </p>
                </div>
                <form onSubmit={handleSearch} style={{ display: 'flex', gap: 10 }}>
                  <input
                    className="form-input"
                    type="text"
                    placeholder={t('jira_issues.search_placeholder')}
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ width: 280 }}
                  />
                  <button type="submit" className="btn btn-ghost">Search</button>
                </form>
              </div>

              {issuesLoading && (
                <p style={{ color: 'var(--text-muted)' }}>{t('jira_issues.loading')}</p>
              )}

              {issuesError && (
                <div className="alert alert-error" style={{ marginBottom: 20 }}>{issuesError}</div>
              )}

              {!issuesLoading && !issuesError && issues.length === 0 && (
                <div style={{ textAlign: 'center', padding: '48px 20px', color: 'var(--text-muted)' }}>
                  <div style={{ fontSize: '2.5rem', marginBottom: 12 }}>📋</div>
                  <p>{t('jira_issues.empty')}</p>
                </div>
              )}

              {!issuesLoading && issues.length > 0 && (
                <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                  {/* Table header */}
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: '110px 1fr 130px 130px 160px 80px 60px',
                    padding: '10px 20px',
                    borderBottom: '1px solid var(--border)',
                    background: 'var(--surface2)',
                  }}>
                    {['Key', 'Summary', 'Status', 'Type', 'Assignee', 'Updated', ''].map((col, i) => (
                      <div key={i} style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '4px 8px' }}>
                        {col}
                      </div>
                    ))}
                  </div>

                  {/* Rows */}
                  {issues.map((issue, idx) => {
                    const ss = statusStyle(issue.status);
                    return (
                      <div
                        key={issue.key}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: '110px 1fr 130px 130px 160px 80px 60px',
                          padding: '13px 20px',
                          borderBottom: idx < issues.length - 1 ? '1px solid var(--border)' : 'none',
                          alignItems: 'center',
                          transition: 'background 0.15s',
                        }}
                        onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = 'var(--surface2)'}
                        onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = 'transparent'}
                      >
                        {/* Key */}
                        <div style={{ padding: '0 8px' }}>
                          <span style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--accent)', background: 'var(--accent-dim)', padding: '3px 8px', borderRadius: 6 }}>
                            {issue.key}
                          </span>
                        </div>

                        {/* Summary */}
                        <div style={{ padding: '0 8px', fontSize: '0.875rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {issue.summary}
                          {issue.has_attachments && (
                            <span title="Has attachments" style={{ marginLeft: 6, fontSize: '0.75rem', color: 'var(--text-dim)' }}>📎</span>
                          )}
                        </div>

                        {/* Status */}
                        <div style={{ padding: '0 8px' }}>
                          <span style={{ padding: '3px 10px', borderRadius: 100, fontSize: '0.73rem', fontWeight: 500, background: ss.bg, color: ss.color }}>
                            {issue.status || '—'}
                          </span>
                        </div>

                        {/* Type */}
                        <div style={{ padding: '0 8px', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                          {issue.issue_type || '—'}
                        </div>

                        {/* Assignee */}
                        <div style={{ padding: '0 8px', fontSize: '0.83rem', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {issue.assignee || '—'}
                        </div>

                        {/* Updated */}
                        <div style={{ padding: '0 8px', fontSize: '0.78rem', color: 'var(--text-dim)' }}>
                          {formatDate(issue.updated_at)}
                        </div>

                        {/* Link */}
                        <div style={{ padding: '0 8px' }}>
                          <Link href={`/integrations/jira/issues/${issue.key}`} className="btn btn-ghost" style={{ padding: '5px 10px', fontSize: '0.78rem' }}>
                            View
                          </Link>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}