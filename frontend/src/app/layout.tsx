import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from '@/lib/auth-context';
import { LangProvider } from '@/lib/lang-context';

export const metadata: Metadata = {
  title: 'Themis',
  description: 'AI Compliance Platform',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <LangProvider>
          <AuthProvider>
            <div className="bg-glow" />
            {children}
          </AuthProvider>
        </LangProvider>
      </body>
    </html>
  );
}