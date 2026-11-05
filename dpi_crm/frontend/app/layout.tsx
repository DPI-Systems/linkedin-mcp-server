import type React from 'react'
import type { Metadata } from 'next'
import { AntdProvider } from '@/components/antd-provider'
import { AppLayout } from '@/components/app-layout'

export const metadata: Metadata = { title: 'DPI CRM' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body style={{ margin: 0 }}>
        <AntdProvider>
          <AppLayout>{children}</AppLayout>
        </AntdProvider>
      </body>
    </html>
  )
}
