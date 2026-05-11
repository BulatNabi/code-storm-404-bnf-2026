import Link from 'next/link';
import Navbar from '@/components/Navbar';

export default function HomePage() {
  return (
    <div className="page">
      <Navbar variant="public" />
      <div className="page-center" style={{ position: 'relative', zIndex: 1 }}>
        <div style={{ textAlign: 'center', maxWidth: 680 }}>
          {/* Tag */}
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 32 }}>
            <span className="tag tag-accent">
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', display: 'inline-block' }} />
              AI-Powered Feature Analysis
            </span>
          </div>

          {/* Headline */}
          <h1 style={{ fontSize: 'clamp(2.4rem, 6vw, 4rem)', marginBottom: 24, letterSpacing: '-0.03em' }}>
            Turn Jira tickets into{' '}
            <span style={{
              background: 'linear-gradient(135deg, var(--accent) 0%, #a78bfa 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}>
              structured insights
            </span>
          </h1>

          <p style={{ color: 'var(--text-muted)', fontSize: '1.1rem', lineHeight: 1.8, marginBottom: 48, fontWeight: 300 }}>
            Connect your Jira workspace, select an issue, and let our AI analyze
            feature descriptions, attachments, and requirements — delivering
            clear analysis in seconds.
          </p>

          {/* CTA */}
          <div style={{ display: 'flex', gap: 16, justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link href="/register" className="btn btn-primary btn-lg">
              Get started free
            </Link>
            <Link href="/login" className="btn btn-ghost btn-lg">
              Log in
            </Link>
          </div>

          {/* Feature pills */}
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap', marginTop: 64 }}>
            {['SSE Streaming', 'Jira Integration', 'Analysis History', 'Multi-project'].map(f => (
              <span key={f} style={{
                padding: '6px 14px',
                borderRadius: '100px',
                border: '1px solid var(--border)',
                fontSize: '0.8rem',
                color: 'var(--text-muted)',
              }}>
                {f}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
