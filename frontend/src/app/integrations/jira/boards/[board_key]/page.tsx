'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import Navbar from '@/components/Navbar';
import AuthGuard from '@/components/AuthGuard';
import { useLang } from '@/lib/lang-context';
import { apiGetJiraBoardIssues, apiGetOrCreateProjectForIssue, extractErrorMessage, type JiraIssue } from '@/lib/api';

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

const GRID = '100px 1fr 115px 100px 130px 85px 125px';

export default function JiraBoardPage() {
  return (
    <AuthGuard>
      <JiraBoardContent />
    </AuthGuard>
  );
}

function JiraBoardContent() {
  const { t } = useLang();
  const params = useParams();
  const router = useRouter();
  const boardKey = decodeURIComponent(String(params.board_key ?? ''));

  const [issues, setIssues] = useState<JiraIssue[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  // Key of the issue whose project is being opened/created (drives the button spinner).
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');

  // "More details": open the issue's project, creating it on first click.
  async function handleMoreDetails(issue: JiraIssue) {
    setBusyKey(issue.key);
    setActionError('');
    try {
      const projectId = await apiGetOrCreateProjectForIssue({ key: issue.key, summary: issue.summary });
      router.push(`/projects/${projectId}`);
    } catch (err) {
      setActionError(extractErrorMessage(err));
      setBusyKey(null);
    }
  }

  const loadIssues = useCallback(async (q?: string) => {
    setLoading(true);
    setError('');
    try {
      const data = await apiGetJiraBoardIssues(boardKey, q);
      setIssues(data.items ?? []);
      setTotal(data.total ?? 0);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [boardKey]);

  useEffect(() => { loadIssues(); }, [loadIssues]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    loadIssues(search);
  }

  return (
    <div className="page">
      <Navbar variant="app" />
      <div style={{ position: 'relative', zIndex: 1, padding: '48px 0 80px' }}>
        <div className="container" style={{ maxWidth: 1000 }}>

          {/* Back + header */}
          <div style={{ marginBottom: 28 }}>
            <Link href="/integrations/jira" className="btn btn-ghost" style={{ marginBottom: 20, display: 'inline-flex' }}>
              ← {t('jira_issues.back')}
            </Link>
            <h1 style={{ fontSize: '2.2rem', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontFamily: 'monospace', color: 'var(--accent)', background: 'var(--accent-dim)', padding: '4px 12px', borderRadius: 8, fontSize: '1.6rem' }}>
                {boardKey}
              </span>
              <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '1.4rem' }}>{t('jira_issues.issues')}</span>
            </h1>
            <p style={{ color: 'var(--text-muted)' }}>{!loading && `${total} ${t('jira_issues.total')}`}</p>
          </div>

          {/* Search */}
          <form onSubmit={handleSearch} style={{ display: 'flex', gap: 10, marginBottom: 24 }}>
            <input
              className="form-input"
              type="text"
              placeholder={t('jira_issues.search_placeholder')}
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ width: 280 }}
            />
            <button type="submit" className="btn btn-ghost">{t('jira_issues.search')}</button>
          </form>

          {loading && (
            <p style={{ color: 'var(--text-muted)' }}>{t('jira_issues.loading')}</p>
          )}

          {error && (
            <div className="alert alert-error" style={{ marginBottom: 20 }}>{error}</div>
          )}

          {actionError && (
            <div className="alert alert-error" style={{ marginBottom: 20 }}>{actionError}</div>
          )}

          {!loading && !error && issues.length === 0 && (
            <div style={{ textAlign: 'center', padding: '48px 20px', color: 'var(--text-muted)' }}>
              <div style={{ fontSize: '2.5rem', marginBottom: 12 }}>📋</div>
              <p>{t('jira_issues.empty')}</p>
            </div>
          )}

          {!loading && issues.length > 0 && (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              {/* Table header */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: GRID,
                padding: '10px 20px',
                borderBottom: '1px solid var(--border)',
                background: 'var(--surface2)',
              }}>
                {[t('jira_issues.col_key'), t('jira_issues.col_summary'), t('jira_issues.col_status'), t('jira_issues.col_type'), t('jira_issues.col_assignee'), t('jira_issues.col_updated'), ''].map((col, i) => (
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
                      gridTemplateColumns: GRID,
                      padding: '13px 20px',
                      borderBottom: idx < issues.length - 1 ? '1px solid var(--border)' : 'none',
                      alignItems: 'center',
                    }}
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

                    {/* More details → open/create the issue's project */}
                    <div style={{ padding: '0 8px' }}>
                      <button
                        className="btn btn-ghost"
                        style={{ padding: '5px 10px', fontSize: '0.76rem', whiteSpace: 'nowrap' }}
                        onClick={() => handleMoreDetails(issue)}
                        disabled={busyKey !== null}
                      >
                        {busyKey === issue.key ? t('jira_issues.opening') : t('jira_issues.more_details')}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
