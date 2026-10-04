import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-50 dark:bg-slate-900">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-teal-600 dark:border-slate-600 dark:border-t-teal-400" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
