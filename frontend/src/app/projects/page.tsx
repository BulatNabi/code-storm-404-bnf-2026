'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import Navbar from '@/components/Navbar';
import AuthGuard from '@/components/AuthGuard';
import { apiGetProjects, apiCreateProject, apiGetJiraStatus, apiConnectJira, apiDisconnectJira } from '@/lib/api';
import { useLang } from '@/lib/lang-context';

interface Project { id: string; name: string; description?: string; }
interface JiraStatus { connected: boolean; domain?: string; email?: string; }

export default function ProjectsPage() {
  return <AuthGuard><ProjectsContent /></AuthGuard>;
}

function ProjectsContent() {
  const { t } = useLang();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [newFiles, setNewFiles] = useState<File[]>([]);
  const [projLoading, setProjLoading] = useState(true);
  const [projError, setProjError] = useState('');
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [createSuccess, setCreateSuccess] = useState('');
  const [jira, setJira] = useState<JiraStatus | null>(null);
  const [jiraLoading, setJiraLoading] = useState(true);
  const [jiraForm, setJiraForm] = useState({ domain: '', email: '', api_token: '' });
  const [jiraConnecting, setJiraConnecting] = useState(false);
  const [jiraError, setJiraError] = useState('');
  const [jiraSuccess, setJiraSuccess] = useState('');

  useEffect(() => { loadProjects(); loadJira(); }, []);

  async function loadProjects() {
    setProjLoading(true); setProjError('');
    try { setProjects(await apiGetProjects()); }
    catch (err: any) { setProjError(err?.detail || 'Failed to load projects.'); }
    finally { setProjLoading(false); }
  }

  async function handleCreateProject(e: React.FormEvent) {
    e.preventDefault(); setCreating(true); setCreateError(''); setCreateSuccess('');
    try {
      const project = await apiCreateProject({
        name: newName,
        description: newDesc,
        files: newFiles,
      });
      // Open the project's chat page so the user can submit feature
      // descriptions. The project description itself is product CONTEXT
      // (e.g. "Mobile bank for retail clients in UZ"), NOT a feature to
      // analyze — the backend already forwards it to ai-core as the
      // `project_description` background field on every /analyze call.
      router.push(`/projects/${project.id}`);
    } catch (err: any) {
      setCreateError(err?.detail || t('projects.create_error_default'));
      setCreating(false);
    }
  }

  async function loadJira() {
    setJiraLoading(true);
    try { setJira(await apiGetJiraStatus()); }
    catch { setJira({ connected: false }); }
    finally { setJiraLoading(false); }
  }

  async function handleConnectJira(e: React.FormEvent) {
    e.preventDefault(); setJiraConnecting(true); setJiraError(''); setJiraSuccess('');
    try { await apiConnectJira(jiraForm); setJiraSuccess(t('projects.jira_success_connected')); loadJira(); }
    catch (err: any) { setJiraError(err?.detail || t('projects.jira_error_connect')); }
    finally { setJiraConnecting(false); }
  }

  async function handleDisconnectJira() {
    setJiraConnecting(true); setJiraError('');
    try { await apiDisconnectJira(); setJira({ connected: false }); setJiraSuccess(t('projects.jira_success_disconnected')); }
    catch (err: any) { setJiraError(err?.detail || t('projects.jira_error_disconnect')); }
    finally { setJiraConnecting(false); }
  }

  return (
    <div className="page">
      <Navbar variant="app" />
      <div style={{ position: 'relative', zIndex: 1, padding: '48px 0 80px' }}>
        <div className="container">
          <div style={{ marginBottom: 40 }}>
            <h1 style={{ fontSize: '2.2rem', marginBottom: 8 }}>{t('projects.title')}</h1>
            <p style={{ color: 'var(--text-muted)' }}>{t('projects.subtitle')}</p>
          </div>

          <div className="grid-3">
            {/* Create */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>✦</span>
                  <h2 style={{ fontSize: '1.1rem' }}>{t('projects.create_title')}</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('projects.create_subtitle')}</p>
              </div>
              <div className="divider" style={{ margin: 0 }} />
              <form onSubmit={handleCreateProject} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {createError && <div className="alert alert-error">{createError}</div>}
                {createSuccess && <div className="alert alert-success">{createSuccess}</div>}
                <div className="form-group">
                  <label className="form-label">{t('projects.create_name')}</label>
                  <input className="form-input" type="text" placeholder={t('projects.create_name_placeholder')}
                    value={newName} onChange={e => setNewName(e.target.value)} required />
                </div>
                <div className="form-group">
                  <label className="form-label">{t('projects.create_desc')}</label>
                  <textarea className="form-input form-textarea" placeholder={t('projects.create_desc_placeholder')}
                    value={newDesc} onChange={e => setNewDesc(e.target.value)} />
                </div>
                <div className="form-group">
                  <label className="form-label">Прикрепить ТЗ / спеку (PDF или DOCX, до 5 файлов)</label>
                  <input
                    className="form-input"
                    type="file"
                    multiple
                    accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    onChange={e => setNewFiles(Array.from(e.target.files || []).slice(0, 5))}
                  />
                  {newFiles.length > 0 && (
                    <div style={{ marginTop: 8, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      {newFiles.map(f => (
                        <div key={f.name}>📎 {f.name} · {(f.size/1024).toFixed(0)} KB</div>
                      ))}
                    </div>
                  )}
                </div>
                <button type="submit" className="btn btn-primary" disabled={creating}>
                  {creating ? t('projects.create_submitting') : t('projects.create_submit')}
                </button>
              </form>
            </div>

            {/* Select */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>⊞</span>
                  <h2 style={{ fontSize: '1.1rem' }}>{t('projects.select_title')}</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('projects.select_subtitle')}</p>
              </div>
              <div className="divider" style={{ margin: 0 }} />
              {projLoading && <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('projects.select_loading')}</p>}
              {projError && <div className="alert alert-error">{projError}</div>}
              {!projLoading && projects.length === 0 && !projError && (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '24px 0' }}>
                  <span style={{ fontSize: '1.8rem' }}>📂</span>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', textAlign: 'center' }}>{t('projects.select_empty')}</p>
                </div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, overflowY: 'auto', maxHeight: 320 }}>
                {projects.map(project => (
                  <Link key={project.id} href={`/projects/${project.id}`}
                    style={{ display: 'block', padding: '14px 16px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', transition: 'border-color 0.2s, transform 0.15s' }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent)'; (e.currentTarget as HTMLElement).style.transform = 'translateX(4px)'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; (e.currentTarget as HTMLElement).style.transform = 'translateX(0)'; }}
                  >
                    <div style={{ fontWeight: 500, marginBottom: 4 }}>{project.name}</div>
                    {project.description && <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project.description}</div>}
                  </Link>
                ))}
              </div>
            </div>

            {/* Jira */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>🔗</span>
                  <h2 style={{ fontSize: '1.1rem' }}>{t('projects.jira_title')}</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('projects.jira_subtitle')}</p>
              </div>
              <div className="divider" style={{ margin: 0 }} />
              {jiraLoading && <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('projects.jira_checking')}</p>}
              {jiraError && <div className="alert alert-error">{jiraError}</div>}
              {jiraSuccess && <div className="alert alert-success">{jiraSuccess}</div>}
              {!jiraLoading && jira?.connected ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div style={{ padding: '14px 16px', background: 'var(--success-dim)', border: '1px solid rgba(52,211,153,0.25)', borderRadius: 'var(--radius)' }}>
                    <div style={{ color: 'var(--success)', fontWeight: 500, marginBottom: 4 }}>✓ {t('projects.jira_connected')}</div>
                    {jira.domain && <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{jira.domain}</div>}
                    {jira.email && <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{jira.email}</div>}
                  </div>
                  <button className="btn btn-danger" onClick={handleDisconnectJira} disabled={jiraConnecting}>
                    {jiraConnecting ? t('projects.jira_disconnecting') : t('projects.jira_disconnect')}
                  </button>
                </div>
              ) : !jiraLoading ? (
                <form onSubmit={handleConnectJira} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div className="form-group">
                    <label className="form-label">{t('projects.jira_domain')}</label>
                    <input className="form-input" type="text" placeholder={t('projects.jira_domain_placeholder')}
                      value={jiraForm.domain} onChange={e => setJiraForm(p => ({ ...p, domain: e.target.value }))} required />
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('projects.jira_email')}</label>
                    <input className="form-input" type="email" placeholder={t('projects.jira_email_placeholder')}
                      value={jiraForm.email} onChange={e => setJiraForm(p => ({ ...p, email: e.target.value }))} required />
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('projects.jira_token')}</label>
                    <input className="form-input" type="password" placeholder={t('projects.jira_token_placeholder')}
                      value={jiraForm.api_token} onChange={e => setJiraForm(p => ({ ...p, api_token: e.target.value }))} required />
                  </div>
                  <button type="submit" className="btn btn-primary" disabled={jiraConnecting}>
                    {jiraConnecting ? t('projects.jira_connecting') : t('projects.jira_connect')}
                  </button>
                </form>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
