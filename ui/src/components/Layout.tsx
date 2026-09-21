import { Outlet, NavLink, useLocation } from 'react-router-dom'
import type { AuthUser } from 'aws-amplify/auth'
import { clsx } from 'clsx'
import GlobalAssistant from './GlobalAssistant'

interface Props {
  user: AuthUser | undefined
  signOut: ((data?: Record<string, string>) => void) | undefined
}

const navItems = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/reviews', label: 'Reviews' },
  { to: '/hitl-decisions', label: 'HITL Decisions' },
  { to: '/upload', label: 'Upload DFMEA' },
  { to: '/ontology', label: 'Knowledge Graph' },
]

export default function Layout({ user, signOut }: Props) {
  const location = useLocation()
  const isGraphWorkspace = location.pathname.startsWith('/ontology')

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top navigation */}
      <header className="bg-brand-900 text-white shadow-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-14">
          <div className="flex items-center gap-8">
            <span className="font-bold text-lg tracking-tight">DFMEA Review</span>
            <nav className="hidden sm:flex gap-1">
              {navItems.map(({ to, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    clsx(
                      'px-3 py-1.5 rounded text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-brand-700 text-white'
                        : 'text-blue-200 hover:bg-brand-700 hover:text-white'
                    )
                  }
                >
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-blue-200 hidden sm:block">{user?.signInDetails?.loginId ?? user?.username}</span>
            <button
              onClick={() => signOut?.()}
              className="px-3 py-1.5 rounded bg-brand-700 hover:bg-brand-600 text-white text-xs font-medium transition-colors"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Page content */}
      <main
        className={isGraphWorkspace
          ? 'flex-1 min-h-0 w-full overflow-hidden'
          : 'flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6'}
      >
        <Outlet />
      </main>

      {!isGraphWorkspace && (
        <footer className="border-t border-gray-200 py-3 text-center text-xs text-gray-400">
          DFMEA Agentic Review System · AIAG-VDA 2019
        </footer>
      )}

      <GlobalAssistant />
    </div>
  )
}
