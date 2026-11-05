'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Layout, Menu, Typography } from 'antd'
import {
  BankOutlined, BellOutlined, HistoryOutlined, HomeOutlined, TeamOutlined,
} from '@ant-design/icons'

const ITEMS = [
  { key: '/', icon: <HomeOutlined />, label: <Link href="/">Inicio</Link> },
  { key: '/novedades', icon: <BellOutlined />, label: <Link href="/novedades">Novedades</Link> },
  { key: '/contactos', icon: <TeamOutlined />, label: <Link href="/contactos">Contactos</Link> },
  { key: '/empresas', icon: <BankOutlined />, label: <Link href="/empresas">Empresas</Link> },
  { key: '/ejecuciones', icon: <HistoryOutlined />, label: <Link href="/ejecuciones">Ejecuciones</Link> },
]

export function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)
  const selected =
    ITEMS.map((i) => i.key).filter((k) => k === '/' ? pathname === '/' : pathname.startsWith(k))[0] || '/'
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider width={220} breakpoint="lg" collapsedWidth={64} collapsed={collapsed} onBreakpoint={setCollapsed} trigger={null} style={{ borderRight: '1px solid #eef0f3' }}>
        <div style={{ padding: collapsed ? '20px 0 12px' : '20px 20px 12px', textAlign: collapsed ? 'center' : 'left' }}>
          <Typography.Text strong style={{ fontSize: 16, whiteSpace: 'nowrap' }}>{collapsed ? 'DPI' : 'DPI CRM'}</Typography.Text>
        </div>
        <Menu mode="inline" selectedKeys={[selected]} items={ITEMS} style={{ borderInlineEnd: 0 }} />
      </Layout.Sider>
      <Layout.Content style={{ padding: collapsed ? 16 : 24, maxWidth: 1400, width: '100%', minWidth: 0 }}>{children}</Layout.Content>
    </Layout>
  )
}
