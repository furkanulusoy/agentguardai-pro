import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { LayoutDashboard, Users, Plug, ShieldCheck, Bot, ScrollText, LogOut, Sun, Moon, PanelLeftClose, PanelLeftOpen, ArrowUpRight, Activity, CircleHelp } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
const items = [
  { to: '/', label: 'Genel bakış', icon: LayoutDashboard, group: 'KONTROL MERKEZİ',permission:'approval.read' },
  { to: '/approvals', label: 'Onay kuyruğu', icon: ShieldCheck,permission:'approval.read' },
  { to: '/executions', label: 'İşlem geçmişi', icon: Activity,permission:'approval.read' },
  { to: '/agents', label: 'Ajanlar', icon: Bot,permission:'tenant.admin' },
  { to: '/policies', label: 'Politikalar', icon: ScrollText,permission:'tenant.admin' },
  { to: '/integrations', label: 'Entegrasyonlar', icon: Plug, group: 'WORKSPACE',permission:'connector.read' },
  { to: '/audit', label: 'Denetim kayıtları', icon: ScrollText,permission:'approval.read' },
  { to: '/team', label: 'Ekip ve erişim', icon: Users,permission:'tenant.admin' },
]
export function Layout() {
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()
  const title = items.find(i => i.to === location.pathname)?.label ?? 'Workspace'
  return <div className={'product-shell ' + (collapsed ? 'nav-collapsed' : '')}>
    <a href="#main-content" className="skip-link">İçeriğe geç</a>
    <aside className="product-sidebar">
      <NavLink to="/" aria-label="AgentGuard genel bakış" className="product-brand"><span className="brand-symbol"><ShieldCheck size={22}/></span><span>AgentGuard<span className="brand-dot">.</span></span></NavLink>
      <div className="workspace-switch"><span className="workspace-avatar">{user?.email[0]?.toUpperCase()}</span><div><strong>Çalışma alanınız</strong><small>{user?.roles.join(' · ')}</small></div></div>
      <nav aria-label="Ana navigasyon">{items.filter(i=>user?.permissions?.includes(i.permission)).map(({to,label,icon: Icon,group}) => <div key={to}>{group && <p className="nav-section-label">{group}</p>}<NavLink end={to === '/'} to={to} aria-label={label} title={label} className={({isActive}) => 'product-nav-link ' + (isActive ? 'active' : '')}><Icon size={17}/><span>{label}</span></NavLink></div>)}</nav>
      <div className="sidebar-bottom"><div className="deployment-card"><span className="tiny-dot"/> SELF-HOSTED<div>Kontrol sizde.<br/><span>Veriler kendi altyapınızda.</span></div></div><button className="product-nav-link" aria-label={theme === "dark" ? "Açık görünüm" : "Koyu görünüm"} onClick={toggleTheme}>{theme === 'dark' ? <Sun size={17}/> : <Moon size={17}/>}<span>{theme === 'dark' ? 'Açık görünüm' : 'Koyu görünüm'}</span></button><button className="product-nav-link" aria-label="Oturumu kapat" onClick={() => void logout()}><LogOut size={17}/><span>Oturumu kapat</span></button><div className="sidebar-account"><span className="workspace-avatar small">{user?.email[0]?.toUpperCase()}</span><span title={user?.email}>{user?.email}</span></div></div>
    </aside>
    <div className="product-body"><header className="product-topbar"><button className="icon-button" aria-label="Menüyü daralt veya genişlet" onClick={() => setCollapsed(!collapsed)}>{collapsed ? <PanelLeftOpen size={18}/> : <PanelLeftClose size={18}/>}</button><span className="breadcrumb">Workspace <span>/</span> <strong>{title}</strong></span><div className="topbar-right"><span className="environment-label">SELF-HOSTED</span><a href="/api/openapi.json" target="_blank" rel="noreferrer"><CircleHelp size={16}/> API sözleşmesi <ArrowUpRight size={13}/></a></div></header><main id="main-content"><Outlet/></main><footer className="product-footer"><span>AgentGuard · AI actions. Human trust.</span><span>İzin ver. Onayla. Denetle.</span></footer></div>
  </div>
}
