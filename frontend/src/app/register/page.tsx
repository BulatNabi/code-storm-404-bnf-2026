'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import Navbar from '@/components/Navbar';
import { apiRegister } from '@/lib/api';
import { useLang } from '@/lib/lang-context';

export default function RegisterPage() {
  const router = useRouter();
  const { t } = useLang();
  const [form, setForm] = useState({ name: '', surname: '', email: '', password: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setForm(prev => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await apiRegister(form);
      router.push('/login?registered=1');
    } catch (err: any) {
      setError(err?.detail || t('register.error_default'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <Navbar variant="register" />
      <div className="page-center">
        <div style={{ width: '100%', maxWidth: 460 }}>
          <div style={{ marginBottom: 32 }}>
            <h1 style={{ fontSize: '2rem', marginBottom: 8 }}>{t('register.title')}</h1>
            <p style={{ color: 'var(--text-muted)' }}>
              {t('register.subtitle')}{' '}
              <Link href="/login" style={{ color: 'var(--accent)' }}>{t('register.subtitle_link')}</Link>
            </p>
          </div>
          <div className="card">
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {error && <div className="alert alert-error">{error}</div>}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div className="form-group">
                  <label className="form-label">{t('register.first_name')}</label>
                  <input className="form-input" type="text" name="name"
                    placeholder={t('register.first_name_placeholder')}
                    value={form.name} onChange={handleChange} required autoComplete="given-name" />
                </div>
                <div className="form-group">
                  <label className="form-label">{t('register.last_name')}</label>
                  <input className="form-input" type="text" name="surname"
                    placeholder={t('register.last_name_placeholder')}
                    value={form.surname} onChange={handleChange} required autoComplete="family-name" />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">{t('register.email')}</label>
                <input className="form-input" type="email" name="email"
                  placeholder={t('register.email_placeholder')}
                  value={form.email} onChange={handleChange} required autoComplete="email" />
              </div>
              <div className="form-group">
                <label className="form-label">{t('register.password')}</label>
                <input className="form-input" type="password" name="password"
                  placeholder={t('register.password_placeholder')}
                  value={form.password} onChange={handleChange} required minLength={8} autoComplete="new-password" />
              </div>
              <button type="submit" className="btn btn-primary" disabled={loading} style={{ marginTop: 4 }}>
                {loading ? t('register.submitting') : t('register.submit')}
              </button>
            </form>
          </div>
          <p style={{ textAlign: 'center', marginTop: 20, fontSize: '0.8rem', color: 'var(--text-dim)' }}>
            {t('register.terms')}
          </p>
        </div>
      </div>
    </div>
  );
}
