'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import Navbar from '@/components/Navbar';
import { apiLogin } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await apiLogin(email, password);
      localStorage.setItem('access_token', data.access_token);
      localStorage.setItem('refresh_token', data.refresh_token);
      router.push('/projects');
    } catch (err: any) {
      setError(err?.detail || 'Invalid email or password. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <Navbar variant="login" />
      <div className="page-center">
        <div style={{ width: '100%', maxWidth: 420 }}>
          {/* Header */}
          <div style={{ marginBottom: 32 }}>
            <h1 style={{ fontSize: '2rem', marginBottom: 8 }}>Welcome back</h1>
            <p style={{ color: 'var(--text-muted)' }}>
              Don&apos;t have an account?{' '}
              <Link href="/register" style={{ color: 'var(--accent)' }}>Register for free</Link>
            </p>
          </div>

          <div className="card">
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {error && <div className="alert alert-error">{error}</div>}

              <div className="form-group">
                <label className="form-label">Email</label>
                <input
                  className="form-input"
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Password</label>
                <input
                  className="form-input"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                />
              </div>

              <button
                type="submit"
                className="btn btn-primary"
                disabled={loading}
                style={{ marginTop: 4 }}
              >
                {loading ? 'Signing in…' : 'Log in'}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
