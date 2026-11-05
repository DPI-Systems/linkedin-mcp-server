'use client'

import React from 'react'
import { App, ConfigProvider } from 'antd'
import esES from 'antd/locale/es_ES'
import { AntdRegistry } from '@ant-design/nextjs-registry'
import dayjs from 'dayjs'
import 'dayjs/locale/es'

dayjs.locale('es')

// DPI Systems
const BRAND = { primary: '#1F4E79', page: '#F5F7FA' }

export function AntdProvider({ children }: { children: React.ReactNode }) {
  return (
    <AntdRegistry>
      <ConfigProvider
        locale={esES}
        theme={{
          token: {
            colorPrimary: BRAND.primary,
            colorLink: BRAND.primary,
            borderRadius: 10,
            fontSize: 14,
            colorBgLayout: BRAND.page,
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          },
          components: {
            Button: { controlHeight: 36, fontWeight: 500 },
            Card: { borderRadiusLG: 14 },
            Layout: { siderBg: '#ffffff', headerBg: '#ffffff' },
          },
        }}
      >
        <App>{children}</App>
      </ConfigProvider>
    </AntdRegistry>
  )
}
