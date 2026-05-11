'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import Navbar from '@/components/Navbar';
import AuthGuard from '@/components/AuthGuard';
import { apiGetProjects, apiCreateProject, apiGetJiraStatus, apiConnectJira, apiDisconnectJira } from '@/lib/api';

interface Project {
  id: string;
  name: string;
  description?: string;
  created_at?: string;
}

interface JiraStatus {
  connected: boolean;
  domain?: string;
  email?: string;
}

export default function ProjectsPage() {
  return (
    <AuthGuard>
      <ProjectsContent />
    </AuthGuard>
  );
}

function ProjectsContent() {
  // Projects state
  const [projects, setProjects] = useState<Project[]>([]);
  const [projLoading, setProjLoading] = useState(true);
  const [projError, setProjError] = useState('');

  // Create project state
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [createSuccess, setCreateSuccess] = useState('');

  // Jira state
  const [jira, setJira] = useState<JiraStatus | null>(null);
  const [jiraLoading, setJiraLoading] = useState(true);
  const [jiraForm, setJiraForm] = useState({ domain: '', email: '', api_token: '' });
  const [jiraConnecting, setJiraConnecting] = useState(false);
  const [jiraError, setJiraError] = useState('');
  const [jiraSuccess, setJiraSuccess] = useState('');

  useEffect(() => {
    loadProjects();
    loadJira();
  }, []);

  async function loadProjects() {
    setProjLoading(true);
    setProjError('');
    try {
      const data = await apiGetProjects();
      setProjects(data);
    } catch (err: any) {
      setProjError(err?.detail || 'Failed to load projects.');
    } finally {
      setProjLoading(false);
    }
  }

  async function handleCreateProject(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError('');
    setCreateSuccess('');
    try {
      const project = await apiCreateProject({ name: newName, description: newDesc });
      setProjects(prev => [...prev, project]);
      setNewName('');
      setNewDesc('');
      setCreateSuccess(`Project "${project.name}" created!`);
    } catch (err: any) {
      setCreateError(err?.detail || 'Failed to create project.');
    } finally {
      setCreating(false);
    }
  }

  async function loadJira() {
    setJiraLoading(true);
    try {
      const data = await apiGetJiraStatus();
      setJira(data);
    } catch {
      setJira({ connected: false });
    } finally {
      setJiraLoading(false);
    }
  }

  async function handleConnectJira(e: React.FormEvent) {
    e.preventDefault();
    setJiraConnecting(true);
    setJiraError('');
    setJiraSuccess('');
    try {
      await apiConnectJira(jiraForm);
      setJiraSuccess('Jira connected successfully!');
      loadJira();
    } catch (err: any) {
      setJiraError(err?.detail || 'Failed to connect Jira.');
    } finally {
      setJiraConnecting(false);
    }
  }

  async function handleDisconnectJira() {
    setJiraConnecting(true);
    setJiraError('');
    try {
      await apiDisconnectJira();
      setJira({ connected: false });
      setJiraSuccess('Jira disconnected.');
    } catch (err: any) {
      setJiraError(err?.detail || 'Failed to disconnect Jira.');
    } finally {
      setJiraConnecting(false);
    }
  }

  return (
    <div className="page">
      <Navbar variant="app" />
      <div style={{ position: 'relative', zIndex: 1, padding: '48px 0 80px' }}>
        <div className="container">
          {/* Page header */}
          <div style={{ marginBottom: 40 }}>
            <h1 style={{ fontSize: '2.2rem', marginBottom: 8 }}>Projects</h1>
            <p style={{ color: 'var(--text-muted)' }}>Manage your analysis projects and integrations</p>
          </div>

          <div className="grid-3">
            {/* ── Block 1: Create project ─────────────────────── */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{
                    width: 32, height: 32, borderRadius: 8,
                    background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem',
                  }}>✦</span>
                  <h2 style={{ fontSize: '1.1rem' }}>Create project</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
                  Start a new analysis project
                </p>
              </div>

              <div className="divider" style={{ margin: '0' }} />

              <form onSubmit={handleCreateProject} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {createError && <div className="alert alert-error">{createError}</div>}
                {createSuccess && <div className="alert alert-success">{createSuccess}</div>}

                <div className="form-group">
                  <label className="form-label">Project name</label>
                  <input
                    className="form-input"
                    type="text"
                    placeholder="My project"
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    required
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Description (optional)</label>
                  <textarea
                    className="form-input form-textarea"
                    placeholder="What is this project about?"
                    value={newDesc}
                    onChange={e => setNewDesc(e.target.value)}
                  />
                </div>

                <button type="submit" className="btn btn-primary" disabled={creating}>
                  {creating ? 'Creating…' : 'Create project'}
                </button>
              </form>
            </div>

            {/* ── Block 2: Select project ─────────────────────── */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{
                    width: 32, height: 32, borderRadius: 8,
                    background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem',
                  }}>⊞</span>
                  <h2 style={{ fontSize: '1.1rem' }}>Select project</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
                  Open an existing project
                </p>
              </div>

              <div className="divider" style={{ margin: '0' }} />

              {projLoading && (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>Loading projects…</p>
              )}
              {projError && <div className="alert alert-error">{projError}</div>}

              {!projLoading && projects.length === 0 && !projError && (
                <div style={{
                  flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
                  justifyContent: 'center', gap: 8, padding: '24px 0',
                }}>
                  <span style={{ fontSize: '1.8rem' }}>📂</span>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', textAlign: 'center' }}>
                    No projects yet. Create one to get started.
                  </p>
                </div>
              )}

              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, overflowY: 'auto', maxHeight: 320 }}>
                {projects.map(project => (
                  <Link
                    key={project.id}
                    href={`/projects/${project.id}`}
                    style={{
                      display: 'block',
                      padding: '14px 16px',
                      background: 'var(--surface2)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius)',
                      transition: 'border-color 0.2s, transform 0.15s',
                    }}
                    onMouseEnter={e => {
                      (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent)';
                      (e.currentTarget as HTMLElement).style.transform = 'translateX(4px)';
                    }}
                    onMouseLeave={e => {
                      (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)';
                      (e.currentTarget as HTMLElement).style.transform = 'translateX(0)';
                    }}
                  >
                    <div style={{ fontWeight: 500, marginBottom: 4 }}>{project.name}</div>
                    {project.description && (
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {project.description}
                      </div>
                    )}
                  </Link>
                ))}
              </div>
            </div>

            {/* ── Block 3: Jira Integration ───────────────────── */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{
                    width: 32, height: 32, borderRadius: 8,
                    background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem',
                  }}>🔗</span>
                  <h2 style={{ fontSize: '1.1rem' }}>Jira integration</h2>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
                  Connect your Jira workspace
                </p>
              </div>

              <div className="divider" style={{ margin: '0' }} />

              {jiraLoading && (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>Checking status…</p>
              )}

              {jiraError && <div className="alert alert-error">{jiraError}</div>}
              {jiraSuccess && <div className="alert alert-success">{jiraSuccess}</div>}

              {!jiraLoading && jira?.connected ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div style={{
                    padding: '14px 16px',
                    background: 'var(--success-dim)',
                    border: '1px solid rgba(52, 211, 153, 0.25)',
                    borderRadius: 'var(--radius)',
                  }}>
                    <div style={{ color: 'var(--success)', fontWeight: 500, marginBottom: 4 }}>✓ Connected</div>
                    {jira.domain && <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{jira.domain}</div>}
                    {jira.email && <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{jira.email}</div>}
                  </div>
                  <button
                    className="btn btn-danger"
                    onClick={handleDisconnectJira}
                    disabled={jiraConnecting}
                  >
                    {jiraConnecting ? 'Disconnecting…' : 'Disconnect Jira'}
                  </button>
                </div>
              ) : !jiraLoading ? (
                <form onSubmit={handleConnectJira} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div className="form-group">
                    <label className="form-label">Jira domain</label>
                    <input
                      className="form-input"
                      type="text"
                      placeholder="yourcompany.atlassian.net"
                      value={jiraForm.domain}
                      onChange={e => setJiraForm(p => ({ ...p, domain: e.target.value }))}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Email</label>
                    <input
                      className="form-input"
                      type="email"
                      placeholder="you@company.com"
                      value={jiraForm.email}
                      onChange={e => setJiraForm(p => ({ ...p, email: e.target.value }))}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">API token</label>
                    <input
                      className="form-input"
                      type="password"
                      placeholder="Your Jira API token"
                      value={jiraForm.api_token}
                      onChange={e => setJiraForm(p => ({ ...p, api_token: e.target.value }))}
                      required
                    />
                  </div>
                  <button type="submit" className="btn btn-primary" disabled={jiraConnecting}>
                    {jiraConnecting ? 'Connecting…' : 'Connect Jira'}
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
