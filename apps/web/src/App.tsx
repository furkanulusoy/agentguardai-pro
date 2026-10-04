import { ExecutionsPage } from './pages/ExecutionsPage'
import { Navigate, Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { AcceptInvitePage } from './pages/AcceptInvitePage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { DashboardHomePage } from './pages/DashboardHomePage'
import { TeamPage } from './pages/TeamPage'
import { PoliciesPage } from './pages/PoliciesPage'
import { AgentsPage } from './pages/AgentsPage'
import { AuditPage } from './pages/AuditPage'
import { IntegrationsPage } from './pages/IntegrationsPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/accept-invite" element={<AcceptInvitePage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />

      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<DashboardHomePage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/policies" element={<PoliciesPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/executions" element={<ExecutionsPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/team" element={<TeamPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
