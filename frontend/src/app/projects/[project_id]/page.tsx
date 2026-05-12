'use client';

import { useState, useEffect, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import Navbar from '@/components/Navbar';
import AuthGuard from '@/components/AuthGuard';
import { apiGetProject, apiUpdateProject, apiDeleteProject } from '@/lib/api';
import { useLang } from '@/lib/lang-context';

interface Project { id: string; name: string; description?: string; created_at?: string; }
interface Message { role: 'user' | 'agent'; content: string; files?: string[]; streaming?: boolean; error?: boolean; }

export default function ProjectDetailPage() {
  return <AuthGuard><ProjectDetailContent /></AuthGuard>;
}

function ProjectDetailContent() {
  const params = useParams();
  const router = useRouter();
  const { t } = useLang();
  const projectId = params.project_id as string;

  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [updateName, setUpdateName] = useState('');
  const [updateDesc, setUpdateDesc] = useState('');
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState('');
  const [updateSuccess, setUpdateSuccess] = useState('');

  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { loadProject(); }, [projectId]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  async function loadProject() {
    setLoading(true); setError('');
    try {
      const data = await apiGetProject(projectId);
      setProject(data); setUpdateName(data.name); setUpdateDesc(data.description || '');
    } catch (err: any) { setError(err?.detail || 'Failed to load project.'); }
    finally { setLoading(false); }
  }

  async function handleUpdate(e: React.FormEvent) {
    e.preventDefault(); setUpdating(true); setUpdateError(''); setUpdateSuccess('');
    try {
      const updated = await apiUpdateProject(projectId, { name: updateName, description: updateDesc });
      setProject(updated); setUpdateSuccess(t('project_detail.update_success'));
    } catch (err: any) { setUpdateError(err?.detail || t('project_detail.update_error_default')); }
    finally { setUpdating(false); }
  }

  async function handleDelete() {
    if (deleteConfirm !== project?.name) return;
    setDeleting(true); setDeleteError('');
    try { await apiDeleteProject(projectId); router.push('/projects'); }
    catch (err: any) { setDeleteError(err?.detail || t('project_detail.delete_error_default')); setDeleting(false); }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(e.target.files || []);
    const valid = selected.filter(f => (f.type === 'application/pdf' || f.name.endsWith('.docx')) && f.size <= 10 * 1024 * 1024);
    setAttachedFiles(prev => [...prev, ...valid].slice(0, 5));
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function removeFile(index: number) { setAttachedFiles(prev => prev.filter((_, i) => i !== index)); }

  async function fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve((reader.result as string).split(',')[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async function handleSend() {
    const text = inputText.trim();
    if (!text && attachedFiles.length === 0) return;
    if (isStreaming) return;
    const fileNames = attachedFiles.map(f => f.name);
    setMessages(prev => [...prev, { role: 'user', content: text, files: fileNames.length ? fileNames : undefined }]);
    let filesBase64: string[] | undefined;
    if (attachedFiles.length > 0) filesBase64 = await Promise.all(attachedFiles.map(fileToBase64));
    setInputText(''); setAttachedFiles([]); setIsStreaming(true);
    setMessages(prev => [...prev, { role: 'agent', content: '', streaming: true }]);
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`http://45.130.127.181:8000/api/projects/${projectId}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ text: text || null, files: filesBase64 || null }),
      });
      if (!response.ok) { const errData = await response.json().catch(() => ({})); throw new Error(errData?.detail || `Error ${response.status}`); }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        for (const line of lines) {
          const data = line.startsWith('data: ') ? line.slice(6) : (!line.startsWith(':') && line.trim() ? line : null);
          if (!data || data === '[DONE]') continue;
          setMessages(prev => { const u = [...prev]; const l = u[u.length - 1]; if (l.role === 'agent') u[u.length - 1] = { ...l, content: l.content + data }; return u; });
        }
      }
    } catch (err: any) {
      setMessages(prev => { const u = [...prev]; u[u.length - 1] = { role: 'agent', content: err?.message || t('project_detail.chat_error_default'), error: true, streaming: false }; return u; });
    } finally {
      setMessages(prev => { const u = [...prev]; const l = u[u.length - 1]; if (l.role === 'agent') u[u.length - 1] = { ...l, streaming: false }; return u; });
      setIsStreaming(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  }

  function handleInputChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInputText(e.target.value);
    const ta = textareaRef.current;
    if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; }
  }

  return (
    <div className="page">
      <Navbar variant="app" />
      <div style={{ position: 'relative', zIndex: 1, padding: '48px 0 80px' }}>
        <div className="container">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 32, fontSize: '0.875rem', color: 'var(--text-muted)' }}>
            <Link href="/projects" style={{ color: 'var(--text-muted)' }}>{t('project_detail.breadcrumb')}</Link>
            <span>/</span>
            <span style={{ color: 'var(--text)' }}>{project?.name || t('project_detail.loading')}</span>
          </div>

          {loading && <p style={{ color: 'var(--text-muted)' }}>{t('project_detail.loading')}</p>}
          {error && <div className="alert alert-error" style={{ marginBottom: 24 }}>{error}</div>}

          {project && (<>
            <div style={{ marginBottom: 40 }}>
              <h1 style={{ fontSize: '2.2rem', marginBottom: 8 }}>{project.name}</h1>
              {project.description && <p style={{ color: 'var(--text-muted)', maxWidth: 600 }}>{project.description}</p>}
              {project.created_at && <p style={{ color: 'var(--text-dim)', fontSize: '0.8rem', marginTop: 8 }}>{t('project_detail.label_created')}: {new Date(project.created_at).toLocaleDateString()}</p>}
            </div>

            <div className="card" style={{ marginBottom: 32 }}>
              <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap' }}>
                <div><div className="form-label" style={{ marginBottom: 4 }}>{t('project_detail.label_id')}</div><code style={{ fontFamily: 'monospace', color: 'var(--accent)', fontSize: '0.875rem' }}>{project.id}</code></div>
                <div><div className="form-label" style={{ marginBottom: 4 }}>{t('project_detail.label_name')}</div><span>{project.name}</span></div>
                {project.description && <div><div className="form-label" style={{ marginBottom: 4 }}>{t('project_detail.label_desc')}</div><span style={{ color: 'var(--text-muted)' }}>{project.description}</span></div>}
              </div>
            </div>

            {/* Chat */}
            <div className="card" style={{ marginBottom: 32, padding: 0, overflow: 'hidden' }}>
              <div style={{ padding: '20px 28px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: 'linear-gradient(135deg, var(--accent), #a78bfa)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>◈</div>
                <div>
                  <div style={{ fontFamily: 'Syne, sans-serif', fontWeight: 600, fontSize: '0.95rem' }}>{t('project_detail.chat_agent_name')}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{t('project_detail.chat_agent_subtitle')}</div>
                </div>
                {isStreaming && (
                  <div style={{ marginLeft: 'auto' }}>
                    <span className="tag tag-accent" style={{ fontSize: '0.7rem' }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', display: 'inline-block', animation: 'pulse 1.2s ease-in-out infinite' }} />
                      {t('project_detail.chat_analyzing')}
                    </span>
                  </div>
                )}
              </div>

              <div style={{ height: 420, overflowY: 'auto', padding: '24px 28px', display: 'flex', flexDirection: 'column', gap: 20 }}>
                {messages.length === 0 && (
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: 'var(--text-dim)', textAlign: 'center' }}>
                    <span style={{ fontSize: '2.5rem' }}>◈</span>
                    <p style={{ fontSize: '0.9rem' }}>{t('project_detail.chat_empty')}</p>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center', marginTop: 4 }}>
                      {(['project_detail.chat_hint1', 'project_detail.chat_hint2', 'project_detail.chat_hint3'] as const).map(key => (
                        <button key={key} onClick={() => setInputText(t(key))}
                          style={{ padding: '6px 12px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 100, fontSize: '0.75rem', color: 'var(--text-muted)', cursor: 'pointer' }}>
                          {t(key)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {messages.map((msg, i) => (
                  <div key={i} style={{ display: 'flex', flexDirection: msg.role === 'user' ? 'row-reverse' : 'row', gap: 12, alignItems: 'flex-start' }}>
                    <div style={{ width: 32, height: 32, borderRadius: 8, flexShrink: 0, background: msg.role === 'user' ? 'var(--accent-dim)' : 'linear-gradient(135deg, var(--accent), #a78bfa)', border: msg.role === 'user' ? '1px solid rgba(108,99,255,0.3)' : 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.8rem', color: msg.role === 'user' ? 'var(--accent)' : '#fff' }}>
                      {msg.role === 'user' ? '↑' : '◈'}
                    </div>
                    <div style={{ maxWidth: '72%', display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {msg.files && msg.files.length > 0 && (
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
                          {msg.files.map((f, fi) => <span key={fi} style={{ padding: '4px 10px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 100, fontSize: '0.72rem', color: 'var(--text-muted)' }}>📎 {f}</span>)}
                        </div>
                      )}
                      <div style={{ padding: '12px 16px', borderRadius: msg.role === 'user' ? '16px 4px 16px 16px' : '4px 16px 16px 16px', background: msg.role === 'user' ? 'var(--accent)' : msg.error ? 'var(--danger-dim)' : 'var(--surface2)', border: msg.role === 'agent' ? `1px solid ${msg.error ? 'rgba(255,77,106,0.25)' : 'var(--border)'}` : 'none', color: msg.role === 'user' ? '#fff' : msg.error ? 'var(--danger)' : 'var(--text)', fontSize: '0.9rem', lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                        {msg.content || (msg.streaming ? '' : '…')}
                        {msg.streaming && <span style={{ display: 'inline-block', width: 8, height: 14, background: 'var(--accent)', borderRadius: 2, marginLeft: 4, verticalAlign: 'middle', animation: 'blink 1s step-end infinite' }} />}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>

              <div style={{ borderTop: '1px solid var(--border)', padding: '16px 20px' }}>
                {attachedFiles.length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                    {attachedFiles.map((f, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 100, fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                        📎 {f.name}
                        <button onClick={() => removeFile(i)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 0, lineHeight: 1, fontSize: '0.9rem' }}>×</button>
                      </div>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
                  <button onClick={() => fileInputRef.current?.click()} disabled={isStreaming || attachedFiles.length >= 5} title={t('project_detail.chat_file_hint')}
                    style={{ width: 40, height: 40, flexShrink: 0, background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 10, cursor: attachedFiles.length >= 5 ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', color: 'var(--text-muted)', opacity: attachedFiles.length >= 5 ? 0.4 : 1 }}>
                    📎
                  </button>
                  <input ref={fileInputRef} type="file" accept=".pdf,.docx" multiple style={{ display: 'none' }} onChange={handleFileChange} />
                  <textarea ref={textareaRef} rows={1} className="form-input" placeholder={t('project_detail.chat_placeholder')}
                    value={inputText} onChange={handleInputChange} onKeyDown={handleKeyDown} disabled={isStreaming}
                    style={{ resize: 'none', overflow: 'hidden', minHeight: 40, maxHeight: 160, lineHeight: '1.5', padding: '10px 14px', flex: 1 }} />
                  <button onClick={handleSend} disabled={isStreaming || (!inputText.trim() && attachedFiles.length === 0)} className="btn btn-primary"
                    style={{ width: 40, height: 40, padding: 0, flexShrink: 0, borderRadius: 10, fontSize: '1.1rem' }}>
                    {isStreaming ? '…' : '↑'}
                  </button>
                </div>
                <p style={{ marginTop: 8, fontSize: '0.72rem', color: 'var(--text-dim)' }}>{t('project_detail.chat_file_hint')}</p>
              </div>
            </div>

            {/* Update / Delete */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
              <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--accent-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>✎</span>
                    <h2 style={{ fontSize: '1.1rem' }}>{t('project_detail.update_title')}</h2>
                  </div>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('project_detail.update_subtitle')}</p>
                </div>
                <div className="divider" style={{ margin: 0 }} />
                <form onSubmit={handleUpdate} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {updateError && <div className="alert alert-error">{updateError}</div>}
                  {updateSuccess && <div className="alert alert-success">{updateSuccess}</div>}
                  <div className="form-group">
                    <label className="form-label">{t('project_detail.update_name')}</label>
                    <input className="form-input" type="text" value={updateName} onChange={e => setUpdateName(e.target.value)} required />
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('project_detail.update_desc')}</label>
                    <textarea className="form-input form-textarea" value={updateDesc} onChange={e => setUpdateDesc(e.target.value)} placeholder={t('project_detail.update_desc_placeholder')} />
                  </div>
                  <button type="submit" className="btn btn-primary" disabled={updating}>
                    {updating ? t('project_detail.update_submitting') : t('project_detail.update_submit')}
                  </button>
                </form>
              </div>

              <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20, borderColor: 'rgba(255,77,106,0.2)' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <span style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--danger-dim)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem' }}>⚠</span>
                    <h2 style={{ fontSize: '1.1rem', color: 'var(--danger)' }}>{t('project_detail.delete_title')}</h2>
                  </div>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('project_detail.delete_subtitle')}</p>
                </div>
                <div className="divider" style={{ margin: 0 }} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {deleteError && <div className="alert alert-error">{deleteError}</div>}
                  <div style={{ padding: '14px 16px', background: 'var(--danger-dim)', border: '1px solid rgba(255,77,106,0.2)', borderRadius: 'var(--radius)', fontSize: '0.875rem', color: 'var(--text-muted)' }}>
                    {t('project_detail.delete_confirm_hint')} <strong style={{ color: 'var(--text)' }}>{project.name}</strong> {t('project_detail.delete_confirm_hint2')}
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('project_detail.delete_confirm_label')}</label>
                    <input className="form-input" type="text" placeholder={project.name} value={deleteConfirm} onChange={e => setDeleteConfirm(e.target.value)}
                      style={{ borderColor: deleteConfirm === project.name ? 'var(--danger)' : undefined }} />
                  </div>
                  <button className="btn btn-danger" onClick={handleDelete} disabled={deleteConfirm !== project.name || deleting}>
                    {deleting ? t('project_detail.delete_submitting') : t('project_detail.delete_submit')}
                  </button>
                </div>
              </div>
            </div>
          </>)}
        </div>
      </div>
      <style>{`
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
      `}</style>
    </div>
  );
}
